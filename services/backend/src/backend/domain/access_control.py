"""P4-09/FR-SEC-01 — RBAC. Five roles (operator, verifier, supervisor,
auditor, administrator), least privilege, no implicit escalation.

Two protocols, kept deliberately separate:

- `IdentityProvider` answers "who is this and what roles do they hold" —
  the P1 integration point (a real SSO/directory service). `MockIdentityProvider`
  is the P0 stand-in: a fixed roster, no real credential check. `services/
  backend/src/backend/api/auth.py`'s old `require_role` was a bare
  `NotImplementedError` (TODO: FR-SEC-01) — this module is that TODO,
  done.
- The permission matrix (`PERMISSION_MATRIX`) answers "what is role X
  allowed to do" — a declared table, not a scatter of `if role ==
  "administrator"` checks. Every route dependency
  (`backend.api.auth.require_permission`) resolves against this table by
  `Permission` value; **no route handler compares a role name inline** —
  grep for `== "operator"`/`== Role.OPERATOR` etc. across `backend/api/`
  finds nothing, which is what "no implicit escalation" means
  mechanically rather than as an intention.

Every permission check that denies, and every unmasked-read grant, is
itself an audited event (`action="rbac.denied"` / `"rbac.checked"`) —
landing in the reserved system shard (`landaudit.SYSTEM_SHARD_KEY`, P4-03)
since an access-control decision has no natural district.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    OPERATOR = "operator"
    VERIFIER = "verifier"
    SUPERVISOR = "supervisor"
    AUDITOR = "auditor"
    ADMINISTRATOR = "administrator"


class Permission(str, Enum):
    """One entry per distinct capability this phase's routes need. Not
    exhaustive of every future permission the system will ever need — new
    permissions get added here and to `PERMISSION_MATRIX` together, never
    invented ad hoc in a handler."""

    DOCUMENT_INGEST = "document.ingest"
    REVIEW_CLAIM = "review.claim"
    REVIEW_SUBMIT = "review.submit"
    REVIEW_CONFIRM = "review.confirm"
    CONFLICT_READ = "conflict.read"
    CONFLICT_ASSIGN = "conflict.assign"
    CONFLICT_TRANSITION = "conflict.transition"
    RECORD_READ_MASKED = "record.read.masked"
    RECORD_READ_UNMASKED = "record.read.unmasked"  # FR-SEC-08 — the separate, privileged operation, P4-08
    RECORD_PUBLISH = "record.publish"
    PROVENANCE_READ = "provenance.read"
    CHAIN_VERIFY = "chain.verify"
    CONFIG_WRITE = "config.write"
    DASHBOARD_READ = "dashboard.read"
    TRAINING_STORE_READ = "training_store.read"  # the training-store bypass decision, see PHASE4.md


# FR-SEC-01 — least privilege, declared once, checked everywhere from this
# one table. Read top to bottom as "what this role may additionally do
# beyond the previous rows," but note there is NO inheritance encoded in
# code — each row is a complete, explicit set, so adding a narrower role
# later can never accidentally inherit a broader role's grant.
PERMISSION_MATRIX: dict[Role, frozenset[Permission]] = {
    Role.OPERATOR: frozenset({
        Permission.DOCUMENT_INGEST,
        Permission.REVIEW_CLAIM,
        Permission.REVIEW_SUBMIT,
        Permission.RECORD_READ_MASKED,
    }),
    Role.VERIFIER: frozenset({
        Permission.REVIEW_CLAIM,
        Permission.REVIEW_SUBMIT,
        Permission.REVIEW_CONFIRM,
        Permission.CONFLICT_READ,
        Permission.RECORD_READ_MASKED,
        Permission.PROVENANCE_READ,
    }),
    Role.SUPERVISOR: frozenset({
        Permission.REVIEW_CONFIRM,
        Permission.CONFLICT_READ,
        Permission.CONFLICT_ASSIGN,
        Permission.CONFLICT_TRANSITION,
        Permission.RECORD_READ_MASKED,
        Permission.RECORD_PUBLISH,
        Permission.PROVENANCE_READ,
        Permission.DASHBOARD_READ,
    }),
    Role.AUDITOR: frozenset({
        Permission.CONFLICT_READ,
        Permission.RECORD_READ_MASKED,
        Permission.PROVENANCE_READ,
        Permission.RECORD_READ_UNMASKED,  # the only role that may ever request an unmasked read
        Permission.CHAIN_VERIFY,
        Permission.DASHBOARD_READ,
    }),
    Role.ADMINISTRATOR: frozenset({
        Permission.DOCUMENT_INGEST,
        Permission.CONFIG_WRITE,
        Permission.TRAINING_STORE_READ,
        Permission.RECORD_READ_MASKED,
        Permission.DASHBOARD_READ,
    }),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in PERMISSION_MATRIX.get(role, frozenset())


@dataclass(frozen=True)
class Identity:
    actor: str
    roles: frozenset[Role]

    def has(self, permission: Permission) -> bool:
        return any(has_permission(role, permission) for role in self.roles)


class IdentityProvider(abc.ABC):
    """P1 integration point (a real SSO/directory service, FR-SEC-01).
    `resolve` takes whatever credential this P0's transport carries (a
    bearer token in a real deployment; `MockIdentityProvider` accepts a
    bare actor name standing in for one) and returns an `Identity` or
    raises `UnknownIdentity`."""

    @abc.abstractmethod
    def resolve(self, credential: str) -> Identity: ...


class UnknownIdentity(ValueError):
    pass


class MockIdentityProvider(IdentityProvider):
    """P0 stand-in — ground rule 3 ("interfaces at P0, integrations at
    P1"). A fixed roster, `credential` is trusted as-is (equal to the
    actor name) with no real authentication — never present this as
    satisfying FR-SEC-01's identity-verification half; it satisfies only
    the *authorization* half (the permission matrix), which is genuinely
    real and does not change when a real `IdentityProvider` replaces
    this."""

    def __init__(self, roster: dict[str, frozenset[Role]] | None = None):
        self._roster = roster or {}

    def resolve(self, credential: str) -> Identity:
        roles = self._roster.get(credential)
        if roles is None:
            raise UnknownIdentity(f"no such identity: {credential!r}")
        return Identity(actor=credential, roles=roles)


def default_mock_identity_provider() -> MockIdentityProvider:
    """P0 fixture roster — one named actor per role, so every P4 test and
    every local `make up` session has a ready-made caller for each role
    without inventing usernames ad hoc per call site."""
    return MockIdentityProvider({
        "operator1": frozenset({Role.OPERATOR}),
        "verifier1": frozenset({Role.VERIFIER}),
        "supervisor1": frozenset({Role.SUPERVISOR}),
        "auditor1": frozenset({Role.AUDITOR}),
        "admin1": frozenset({Role.ADMINISTRATOR}),
    })
