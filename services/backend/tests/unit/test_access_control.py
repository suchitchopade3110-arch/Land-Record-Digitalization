"""P4-09/FR-SEC-01 — RBAC. Pure, no DB: the permission matrix and
`IdentityProvider` are plain Python."""
import ast
from pathlib import Path

import pytest
from backend.domain.access_control import (
    PERMISSION_MATRIX,
    Identity,
    MockIdentityProvider,
    Permission,
    Role,
    UnknownIdentity,
    default_mock_identity_provider,
    has_permission,
)


def test_every_role_has_a_declared_permission_set():
    for role in Role:
        assert role in PERMISSION_MATRIX


def test_least_privilege_operator_cannot_publish_or_read_unmasked():
    assert has_permission(Role.OPERATOR, Permission.RECORD_PUBLISH) is False
    assert has_permission(Role.OPERATOR, Permission.RECORD_READ_UNMASKED) is False


def test_only_auditor_holds_unmasked_read():
    holders = [role for role in Role if has_permission(role, Permission.RECORD_READ_UNMASKED)]
    assert holders == [Role.AUDITOR]


def test_only_administrator_holds_config_write():
    holders = [role for role in Role if has_permission(role, Permission.CONFIG_WRITE)]
    assert holders == [Role.ADMINISTRATOR]


def test_d1_permissions_holders_match_least_privilege():
    assert [r for r in Role if has_permission(r, Permission.DOCUMENT_INGEST)] == [Role.OPERATOR, Role.ADMINISTRATOR]
    assert [r for r in Role if has_permission(r, Permission.REVIEW_CONFIRM)] == [Role.VERIFIER, Role.SUPERVISOR]
    assert [r for r in Role if has_permission(r, Permission.CONFLICT_READ)] == [Role.VERIFIER, Role.SUPERVISOR, Role.AUDITOR]
    assert [r for r in Role if has_permission(r, Permission.DASHBOARD_READ)] == [Role.SUPERVISOR, Role.AUDITOR, Role.ADMINISTRATOR]


def test_no_role_holds_every_permission_no_implicit_superuser():
    all_permissions = set(Permission)
    for role in Role:
        assert PERMISSION_MATRIX[role] != frozenset(all_permissions)


def test_mock_identity_provider_resolves_a_known_actor():
    provider = MockIdentityProvider({"alice": frozenset({Role.OPERATOR})})
    identity = provider.resolve("alice")
    assert identity.actor == "alice"
    assert Role.OPERATOR in identity.roles


def test_mock_identity_provider_rejects_an_unknown_actor():
    provider = MockIdentityProvider({})
    with pytest.raises(UnknownIdentity):
        provider.resolve("nobody")


def test_default_roster_has_exactly_one_actor_per_role():
    provider = default_mock_identity_provider()
    for role in Role:
        matching = [name for name, roles in provider._roster.items() if role in roles]
        assert len(matching) == 1, f"expected exactly one default actor for {role}, found {matching}"


def test_identity_has_reflects_the_permission_matrix():
    identity = Identity(actor="x", roles=frozenset({Role.SUPERVISOR}))
    assert identity.has(Permission.CONFLICT_ASSIGN) is True
    assert identity.has(Permission.RECORD_READ_UNMASKED) is False


def test_no_route_handler_compares_a_role_name_inline():
    """FR-SEC-01's "no role name ever compared inline in a handler" —
    checked mechanically: parse every `backend/api/*.py` file's AST and
    confirm no `Compare` node has a `Role` member (or its string value)
    on either side. A handler is only allowed to gate on `Permission` via
    `require_permission`, never re-derive its own role check."""
    api_dir = Path(__file__).resolve().parents[2] / "src" / "backend" / "api"
    role_string_values = {r.value for r in Role}

    for path in api_dir.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for operand in (node.left, *node.comparators):
                    if isinstance(operand, ast.Constant) and operand.value in role_string_values:
                        pytest.fail(f"{path}: role-name string compared inline in a handler: {ast.dump(node)}")
                    if isinstance(operand, ast.Attribute) and operand.attr in {r.name for r in Role}:
                        pytest.fail(f"{path}: Role member compared inline in a handler: {ast.dump(node)}")
