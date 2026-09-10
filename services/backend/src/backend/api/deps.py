"""P4-09b — the one `get_session` dependency every `api/*.py` router (and
`backend.api.auth.require_permission`) imports, instead of each file
declaring its own local copy of the same three lines.

Before this module existed, `require_permission`'s dependency opened its
own session directly via `backend.models.base.session_factory()` — not
through a `Depends(...)`-injected callable at all — so a live-route test
overriding a route's own `get_session` (via
`app.dependency_overrides[...]`) left the permission check's internal
audit write still pointed at whatever `DATABASE_URL` the process
happened to have, unreachable by that override. Three test files (P5-02b's
`test_config_version_write_workflow.py`, P4-10b's
`test_live_route_masking.py`) worked around this by aligning
`DATABASE_URL` itself with the test database for their whole module
instead — a real accommodation, but one every future live-route test
would otherwise have had to rediscover and repeat.

Routing every router's session *and* `require_permission`'s through this
one shared callable fixes that: FastAPI caches a dependency's result per
request (same callable, same arguments), so a route that also declares
`session: Session = Depends(get_session)` shares the exact session
`require_permission` used for its own audit write — one override point
(`app.dependency_overrides[get_session] = ...`) now covers every request
a route or its permission check makes, with no `DATABASE_URL` alignment
needed at all.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from backend.models.base import session_factory


def get_session() -> Generator[Session, None, None]:
    factory = session_factory()
    with factory() as session:
        yield session
