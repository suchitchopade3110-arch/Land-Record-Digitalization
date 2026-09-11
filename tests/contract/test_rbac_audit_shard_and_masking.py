"""T1-01 / Block D — Audit check for RBAC checked/denied entries.

Verifies:
1. rbac.checked / rbac.denied entries for newly guarded Phase 2/3 routes land in the system shard (SYSTEM_SHARD_KEY).
2. rbac audit entries carry NO personal-data field values (value_hash is None / subject is the permission string).
3. Confirm route rejection writes correction.confirm_rejected into audit trail without leaking field values.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from landaudit import SYSTEM_SHARD_KEY

from backend.api import auth, deps
from backend.domain.access_control import (
    MockIdentityProvider,
    Permission,
    Role,
)
from backend.domain.correction import confirm_pending_correction, SameActorCannotConfirm
from backend.main import app

ROLE_ROSTER = {
    "op1": frozenset({Role.OPERATOR}),
    "auditor1": frozenset({Role.AUDITOR}),
    "verifier1": frozenset({Role.VERIFIER}),
}


@pytest.fixture
def audit_test_client():
    provider = MockIdentityProvider(ROLE_ROSTER)
    auth.set_identity_provider(provider)
    mock_session = MagicMock()
    app.dependency_overrides[deps.get_session] = lambda: mock_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, mock_session
    finally:
        app.dependency_overrides.pop(deps.get_session, None)
        auth.set_identity_provider(auth.default_mock_identity_provider())


def test_rbac_denied_lands_in_system_shard_with_no_field_values(audit_test_client):
    """Broken implementation caught: An RBAC denial logging into a tenant/district shard
    or carrying sensitive payload values in subject/value_hash.
    """
    client, mock_session = audit_test_client

    with patch("backend.domain.audit_log.chain_append") as mock_append:
        # Operator has no DASHBOARD_READ permission
        resp = client.get("/dashboard/metrics", headers={"X-Actor": "op1"})
        assert resp.status_code == 403

        # Verify audit append was called for rbac.denied
        mock_append.assert_called_once()
        _, kwargs = mock_append.call_args
        assert kwargs["actor"] == "op1"
        assert kwargs["action"] == "rbac.denied"
        assert kwargs["subject"] == Permission.DASHBOARD_READ.value
        assert kwargs["shard_key"] == SYSTEM_SHARD_KEY
        assert kwargs.get("value_hash") is None
        assert kwargs.get("purpose") is None


def test_rbac_checked_lands_in_system_shard_with_no_field_values(audit_test_client):
    """Broken implementation caught: An RBAC grant logging into the wrong shard
    or carrying field values.
    """
    client, mock_session = audit_test_client

    with patch("backend.domain.audit_log.chain_append") as mock_append, \
         patch("backend.api.dashboard.get_page_activity", return_value=[]):
        # Auditor has DASHBOARD_READ permission
        resp = client.get("/dashboard/metrics", headers={"X-Actor": "auditor1"})
        assert resp.status_code == 200

        # Verify audit append was called for rbac.checked
        mock_append.assert_called_once()
        _, kwargs = mock_append.call_args
        assert kwargs["actor"] == "auditor1"
        assert kwargs["action"] == "rbac.checked"
        assert kwargs["subject"] == Permission.DASHBOARD_READ.value
        assert kwargs["shard_key"] == SYSTEM_SHARD_KEY
        assert kwargs.get("value_hash") is None


def test_maker_checker_confirm_rejection_audit_entry():
    """Broken implementation caught: Self-confirmation rejection not recording audit event
    or recording raw field values instead of extraction id subject.
    """
    mock_session = MagicMock()
    mock_session.execute.return_value.scalars.return_value.first.return_value = None
    mock_pending = MagicMock()
    mock_pending.id = "pending-123"
    mock_pending.state = "pending"
    mock_pending.first_actor = "verifier1"
    mock_pending.extraction_id = "ext-456"
    mock_session.get.return_value = mock_pending

    with patch("backend.domain.correction.audit_append") as mock_append:
        with pytest.raises(SameActorCannotConfirm):
            confirm_pending_correction(mock_session, "pending-123", actor="verifier1")

        mock_append.assert_called_once()
        _, kwargs = mock_append.call_args
        assert kwargs["actor"] == "verifier1"
        assert kwargs["action"] == "correction.confirm_rejected"
        assert kwargs["subject"] == "ext-456"
        assert kwargs["purpose"] == "maker_checker_confirm"
        assert kwargs.get("value_hash") is None
