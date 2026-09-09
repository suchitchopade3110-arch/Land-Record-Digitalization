"""Reads config from the Config Service (M13) at startup + on invalidation
event — never polls only.

Wraps observability.ConfigClient (shared cache-by-config_version behavior)
around this service's own Config Service implementation (it IS the owner —
other services call it over HTTP; this module is the in-process read path
used by backend's own workers, e.g. the triage router, which is one of
the two callers CLAUDE.md invariant 3 / FR-CFG-02 allows to resolve
"current" rather than reading a pinned envelope).

P5-04's event-based cache invalidation (broker version-change events, with
a timer only as backstop, fail loud if the event channel is unavailable at
startup) is not implemented here yet — `observability.ConfigClient` only
re-fetches on every call and diffs `config_version` client-side, which is
correct but does not yet meet P5-04's "never poll as the sole mechanism"
bar for a shared library. That's P5-04's own task, not a P5-01/03
side effect.
"""
from __future__ import annotations

from observability import ConfigClient

from backend.domain.config_versions import (
    ConfigNotFound,
    as_response,
    get_effective_config,
)
from backend.models.base import session_factory


def _fetch_from_db(scope: str, key: str) -> dict:
    """FR-CFG-01 — read the currently-effective `ConfigVersion` row for
    (scope, key) from Postgres, in the exact §4.1 response shape
    `ConfigClient` expects."""
    factory = session_factory()
    with factory() as session:
        try:
            row = get_effective_config(session, scope, key)
        except ConfigNotFound as exc:
            raise KeyError(f"no effective config for scope={scope!r}, key={key!r}") from exc
        return as_response(row)


config_client = ConfigClient(fetch=_fetch_from_db)
