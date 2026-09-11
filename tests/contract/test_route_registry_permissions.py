"""T1-01 / Block B — Route registry permissions contract tests.

Verifies:
1. Every mounted route in backend.main.app has a corresponding row in gateway/route_registry.yaml.
2. Every row in gateway/route_registry.yaml carries an explicit `permission:` key.
3. For every mounted backend route x every role in PERMISSION_MATRIX, the response
   is 2xx/4xx-business or 403 exactly as the registry's `permission:` declares.
"""
from __future__ import annotations

from pathlib import Path
import re
from unittest.mock import MagicMock, patch
import pytest
import yaml
from fastapi.testclient import TestClient

from backend.domain.access_control import (
    PERMISSION_MATRIX,
    Identity,
    MockIdentityProvider,
    Permission,
    Role,
)
from backend.api import auth, deps
from backend.main import app

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "gateway" / "route_registry.yaml"

ROLE_ACTORS = {
    Role.OPERATOR: "operator1",
    Role.VERIFIER: "verifier1",
    Role.SUPERVISOR: "supervisor1",
    Role.AUDITOR: "auditor1",
    Role.ADMINISTRATOR: "admin1",
}


def _routes_from_registry() -> list[dict]:
    with open(REGISTRY_PATH) as f:
        doc = yaml.safe_load(f)
    return doc["routes"]


def _mounted_backend_routes() -> list[tuple[str, str]]:
    paths = app.openapi()["paths"]
    routes = []
    for path, methods in paths.items():
        for method in methods:
            m = method.upper()
            if m not in ("HEAD", "OPTIONS"):
                routes.append((m, path))
    return sorted(routes)


def _resolve_path_placeholders(path: str) -> str:
    values = {
        "extraction_id": "00000000-0000-0000-0000-000000000001",
        "record_group_id": "00000000-0000-0000-0000-000000000002",
        "scope": "global",
        "key": "test_key",
        "type": "district",
        "task_id": "00000000-0000-0000-0000-000000000003",
        "conflict_id": "00000000-0000-0000-0000-000000000004",
        "pending_id": "00000000-0000-0000-0000-000000000005",
    }
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), "placeholder")), path)


def test_every_mounted_backend_route_has_registry_row_with_permission_key():
    """Broken implementation caught: A newly mounted backend route shipping without
    a corresponding row in gateway/route_registry.yaml or missing an explicit permission key.
    """
    registry_routes = _routes_from_registry()
    mounted_routes = _mounted_backend_routes()

    for method, path in mounted_routes:
        if path == "/healthz":
            continue  # Health probe exempted from gateway registry
        matching = [
            r for r in registry_routes
            if r.get("path") == path and r.get("method") == method and r.get("service") == "backend"
        ]
        assert matching, f"Mounted backend route {method} {path} has no matching row in gateway/route_registry.yaml"
        row = matching[0]
        assert "permission" in row, (
            f"Route {method} {path} in gateway/route_registry.yaml is missing the mandatory `permission:` key"
        )
        assert row["permission"] is not None, (
            f"Route {method} {path} in gateway/route_registry.yaml has null `permission:` key"
        )


@pytest.fixture
def test_client():
    roster = {actor: frozenset({role}) for role, actor in ROLE_ACTORS.items()}
    provider = MockIdentityProvider(roster)
    auth.set_identity_provider(provider)
    mock_session = MagicMock()
    mock_session.execute.return_value.scalars.return_value.first.return_value = None
    mock_session.execute.return_value.scalar_one_or_none.return_value = None
    mock_session.execute.return_value.scalars.return_value.all.return_value = []
    mock_session.get.return_value = None
    mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    app.dependency_overrides[deps.get_session] = lambda: mock_session

    with patch("backend.api.config_service.get_pinned_config", return_value={"key": "k", "value": {}}), \
         patch("backend.api.config_service.get_effective_config", return_value={"key": "k", "value": {}}), \
         patch("backend.api.config_service.as_response", return_value={"key": "k", "value": {}}), \
         patch("backend.api.closed_sets.get_closed_set", return_value={"type": "district", "entries": []}), \
         patch("backend.api.dashboard.get_page_activity", return_value=[]), \
         patch("backend.api.conflicts.list_conflicts", return_value=[]):
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client
        finally:
            app.dependency_overrides.pop(deps.get_session, None)
            auth.set_identity_provider(auth.default_mock_identity_provider())


_BACKEND_CASES = [
    (method, path, role, ROLE_ACTORS[role])
    for method, path in _mounted_backend_routes()
    if path != "/healthz"
    for role in Role
]


@pytest.mark.parametrize(
    "method,path,role,actor",
    _BACKEND_CASES,
    ids=[f"{m}_{p}_as_{r.value}" for m, p, r, _ in _BACKEND_CASES],
)
def test_route_rbac_matches_registry_permission(test_client, method, path, role, actor):
    """Broken implementation caught: A route handler not guarded by require_permission,
    or guarded by the wrong permission, allowing unauthorized roles or blocking authorized roles.
    """
    registry_routes = _routes_from_registry()
    matching = [
        r for r in registry_routes
        if r.get("path") == path and r.get("method") == method and r.get("service") == "backend"
    ]
    assert matching, f"Missing registry row for {method} {path}"
    registry_permission = matching[0].get("permission")
    assert registry_permission is not None, f"Registry row for {method} {path} has no permission"

    resolved_path = _resolve_path_placeholders(path)

    # Make request with X-Actor header
    headers = {"X-Actor": actor}
    if method == "GET":
        resp = test_client.get(resolved_path, headers=headers)
    elif method == "POST":
        resp = test_client.post(resolved_path, json={}, headers=headers)
    else:
        resp = test_client.request(method, resolved_path, headers=headers)

    if registry_permission == "service_read_no_personal_data":
        # Unauthenticated service read: should not be 403
        assert resp.status_code != 403, f"{method} {path} returned 403 for service read"
    else:
        # Resolve permission enum
        perm = Permission(registry_permission)
        allowed = perm in PERMISSION_MATRIX.get(role, frozenset())
        if allowed:
            # Should not be 403 forbidden
            assert resp.status_code != 403, (
                f"Expected role {role.value} to be ALLOWED on {method} {path} (perm={perm.value}), "
                f"but got 403: {resp.text}"
            )
        else:
            # Must be 403 forbidden
            assert resp.status_code == 403, (
                f"Expected role {role.value} to be DENIED (403) on {method} {path} (perm={perm.value}), "
                f"but got {resp.status_code}: {resp.text}"
            )
