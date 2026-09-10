"""RBAC dependency wiring — FR-SEC-01, P4-09. The identity/permission
logic itself lives in `backend.domain.access_control` (`IdentityProvider`,
`PERMISSION_MATRIX`); this module is only the FastAPI-shaped adapter: read
a credential off the request, resolve it, check the one permission a route
declares it needs, audit the check, raise 401/403 on failure.

`require_permission(Permission.X)` is what every P4 route depends on —
never a bare `Role` comparison in a handler body. `grep -rn '== Role\\.' services/backend/src/backend/api`
finds nothing, which is the mechanical version of "no role name ever
compared inline in a handler."

P0 transport: the credential is a plain `X-Actor` header, trusted as the
identity provider's `resolve()` input as-is — this is
`MockIdentityProvider`'s whole posture (see that class's docstring), not
a real bearer-token/session mechanism. Swapping `_identity_provider` for a
real `IdentityProvider` is the entire P1 migration; no route changes.

P4-09b: the permission-check audit write goes through the shared
`backend.api.deps.get_session` dependency, not a private
`session_factory()` call — see that module's docstring for why. FastAPI
caches a dependency's result per request, so a route declaring its own
`session: Session = Depends(get_session)` shares this exact session; a
test overriding `get_session` therefore overrides the permission check's
session too, with no separate `DATABASE_URL` alignment needed.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_session
from backend.domain.access_control import (
    Identity,
    IdentityProvider,
    Permission,
    Role,
    UnknownIdentity,
    default_mock_identity_provider,
)

__all__ = ["Permission", "Role", "get_identity", "require_permission"]

_identity_provider: IdentityProvider = default_mock_identity_provider()


def set_identity_provider(provider: IdentityProvider) -> None:
    """Test/deployment hook — swaps the process-wide provider. Kept as an
    explicit setter (not a constructor argument threaded through every
    route) so `main.py` wires exactly one real implementation at startup
    without every router needing to know about it."""
    global _identity_provider
    _identity_provider = provider


def get_identity(x_actor: str = Header(...)) -> Identity:
    try:
        return _identity_provider.resolve(x_actor)
    except UnknownIdentity as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


def require_permission(permission: Permission):
    """FastAPI dependency factory — `Depends(require_permission(Permission.RECORD_PUBLISH))`.
    Every call is itself audited (`backend.domain.audit_log.record_permission_check`)
    — a denial is exactly as loggable an event as a grant, since "who
    tried to do what and was refused" is part of FR-SEC-01's own record,
    not just successes.
    """

    def _dependency(
        identity: Identity = Depends(get_identity), session: Session = Depends(get_session),
    ) -> Identity:
        from backend.domain.audit_log import record_permission_check

        allowed = identity.has(permission)
        # Committed here, independently of whatever the route does
        # afterward with this same (shared, per-request) session — a
        # denial raises immediately below and the route body never runs
        # at all, so this check's own audit entry must be durable on its
        # own rather than riding on a commit that may never happen.
        record_permission_check(session, actor=identity.actor, permission=permission.value, allowed=allowed)
        session.commit()
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail=f"actor {identity.actor!r} lacks permission {permission.value!r}",
            )
        return identity

    return _dependency
