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
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException

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

    def _dependency(identity: Identity = Depends(get_identity)) -> Identity:
        from backend.domain.audit_log import record_permission_check
        from backend.models.base import session_factory

        allowed = identity.has(permission)
        factory = session_factory()
        with factory() as session:
            record_permission_check(session, actor=identity.actor, permission=permission.value, allowed=allowed)
            session.commit()
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail=f"actor {identity.actor!r} lacks permission {permission.value!r}",
            )
        return identity

    return _dependency
