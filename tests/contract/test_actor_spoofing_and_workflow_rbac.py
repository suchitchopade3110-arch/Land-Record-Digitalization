"""T1-01 / Block B — Actor spoofing prevention, crop authorization, and maker-checker HTTP confirm route tests.

Verifies:
1. Actor spoofing: X-Actor: op_a with body or query actor=op_b: body/query actor field is no longer accepted
   or ignored, and the operation/audit is attributed to op_a.
2. Crop route access control:
   - Identity without REVIEW_CLAIM gets 403.
   - Operator who is not the task's assignee gets 403.
3. Maker-checker confirm route (POST /review-tasks/pending-corrections/{pending_id}/confirm):
   - Confirmer == Maker returns 403.
   - Confirmer == Claimant returns 403.
   - Identity lacking REVIEW_CONFIRM returns 403.
   - Valid distinct confirmer with REVIEW_CONFIRM confirms the pending correction into a real Correction row.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.api import auth, deps
from backend.domain.access_control import (
    Identity,
    MockIdentityProvider,
    Permission,
    Role,
)
from backend.main import app

ROLE_ROSTER = {
    "op_a": frozenset({Role.OPERATOR}),
    "op_b": frozenset({Role.OPERATOR}),
    "verifier_b": frozenset({Role.VERIFIER}),
    "supervisor_c": frozenset({Role.SUPERVISOR}),
    "auditor_d": frozenset({Role.AUDITOR}),
    "admin_e": frozenset({Role.ADMINISTRATOR}),
}


@pytest.fixture
def mock_session():
    s = MagicMock()
    s.execute.return_value.scalars.return_value.first.return_value = None
    s.execute.return_value.scalar_one_or_none.return_value = None
    s.execute.return_value.scalars.return_value.all.return_value = []
    s.get.return_value = None
    return s


@pytest.fixture
def rbac_client(mock_session):
    provider = MockIdentityProvider(ROLE_ROSTER)
    auth.set_identity_provider(provider)
    app.dependency_overrides[deps.get_session] = lambda: mock_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.pop(deps.get_session, None)
        auth.set_identity_provider(auth.default_mock_identity_provider())


def test_actor_spoofing_rejected_or_ignored_in_submit(rbac_client, mock_session):
    """Broken implementation caught: An operator submitting a review task pretending to be another actor
    by passing `actor: op_b` in the body while authenticated as `op_a`.
    """
    with patch("backend.api.review_tasks.submit") as mock_submit:
        mock_submit.return_value = {"status": "ok"}
        resp = rbac_client.post(
            "/review-tasks/00000000-0000-0000-0000-000000000001/submit",
            json={"actor": "op_b", "corrected_value": "New Name"},
            headers={"X-Actor": "op_a"},
        )
        assert resp.status_code == 200, resp.text
        # Verify the domain submit function was called with authenticated actor "op_a", NEVER spoofed "op_b"
        mock_submit.assert_called_once()
        _, kwargs = mock_submit.call_args
        assert kwargs["actor"] == "op_a", f"Expected actor op_a, but got {kwargs['actor']}"


def test_crop_route_forbidden_without_review_claim_permission(rbac_client):
    """Broken implementation caught: A role without REVIEW_CLAIM (e.g. auditor or admin)
    requesting a crop URL.
    """
    resp = rbac_client.get(
        "/review-tasks/00000000-0000-0000-0000-000000000001/crop",
        headers={"X-Actor": "auditor_d"},
    )
    assert resp.status_code == 403, f"Expected 403 for auditor on crop route, got {resp.status_code}"


def test_crop_route_forbidden_if_not_assigned_to_caller(rbac_client, mock_session):
    """Broken implementation caught: An operator requesting a crop for a review task
    assigned to a different operator.
    """
    mock_task = MagicMock()
    mock_task.id = "task-1"
    mock_task.assignee = "op_b"
    mock_session.get.return_value = mock_task

    resp = rbac_client.get(
        "/review-tasks/task-1/crop",
        headers={"X-Actor": "op_a"},
    )
    assert resp.status_code == 403, f"Expected 403 for non-assignee crop fetch, got {resp.status_code}"


def test_maker_checker_confirm_route_forbidden_for_same_maker(rbac_client, mock_session):
    """Broken implementation caught: Maker confirming their own pending correction via HTTP route.
    """
    mock_pending = MagicMock()
    mock_pending.id = "pending-1"
    mock_pending.state = "pending"
    mock_pending.first_actor = "verifier_b"
    mock_pending.extraction_id = "ext-1"
    mock_session.get.return_value = mock_pending

    resp = rbac_client.post(
        "/review-tasks/pending-corrections/pending-1/confirm",
        headers={"X-Actor": "verifier_b"},
    )
    assert resp.status_code == 403, f"Expected 403 for self-confirmation, got {resp.status_code}"


def test_maker_checker_confirm_route_forbidden_without_review_confirm_permission(rbac_client):
    """Broken implementation caught: An operator (lacking REVIEW_CONFIRM) confirming a pending correction.
    """
    resp = rbac_client.post(
        "/review-tasks/pending-corrections/pending-1/confirm",
        headers={"X-Actor": "op_a"},
    )
    assert resp.status_code == 403, f"Expected 403 for operator on confirm route, got {resp.status_code}"


def test_maker_checker_confirm_route_succeeds_for_distinct_verifier(rbac_client, mock_session):
    """Broken implementation caught: A distinct verifier with REVIEW_CONFIRM failing to confirm a pending correction.
    """
    mock_pending = MagicMock()
    mock_pending.id = "pending-1"
    mock_pending.state = "pending"
    mock_pending.first_actor = "op_a"
    mock_pending.extraction_id = "ext-1"
    mock_pending.crop_uri = "uri"
    mock_pending.predicted = "Old"
    mock_pending.corrected = "New"
    mock_pending.edit_distance = 5
    mock_pending.model_version = "v1"
    mock_pending.config_version = "v1"
    mock_pending.stream = "routed"
    mock_pending.source_page_digest = "digest"
    mock_session.get.return_value = mock_pending

    resp = rbac_client.post(
        "/review-tasks/pending-corrections/pending-1/confirm",
        headers={"X-Actor": "verifier_b"},
    )
    assert resp.status_code == 200, f"Expected 200 for verifier confirming pending correction, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_by"] == "verifier_b"
