"""Reads config from the Config Service (M13) at startup + on invalidation
event — never polls only (P5-04, contract §4.1).

This is backend's own in-process read path: the triage router and the
impact-preview job (the two callers CLAUDE.md invariant 3 / FR-CFG-02
allows to resolve "current" config) use it, and `api/closed_sets.py`
reads its corpus-version pointer through it too (P5-05) — one cache, one
invalidation path, for backend's own process, the same shape Shree's,
Shruthi's and Tharun's services get from their own `landconfigclient`
instances wrapping an HTTP `fetch` instead of this in-process one.

Supersedes the interim `observability.ConfigClient` this module used
before P5-04 landed — that stub only diffed `config_version` on every
call and never met §4.1's "never poll on a timer as the sole
invalidation mechanism" bar. `observability.ConfigClient` is left as-is
for the other three services still importing it directly; migrating them
to `landconfigclient` is a follow-up for each of those owners, not done
here.
"""
from __future__ import annotations

from landconfigclient import ConfigClient

from backend.domain.config_versions import (
    as_response,
    get_effective_config,
    get_pinned_config,
)
from backend.models.base import session_factory


def _fetch_from_db(scope: str, key: str, config_version: str | None) -> dict:
    """FR-CFG-01 — read the requested `ConfigVersion` row for (scope, key)
    from Postgres, in the exact §4.1 response shape `ConfigClient`
    expects. Pinned when `config_version` is given, "effective now"
    otherwise — the same two forms `api/config_service.py`'s own HTTP
    route exposes, called in-process here. `ConfigNotFound` propagates
    unchanged — callers (e.g. `backend.domain.closed_sets`) catch it by
    name rather than have it wrapped into something generic.
    """
    factory = session_factory()
    with factory() as session:
        row = (
            get_pinned_config(session, scope, key, config_version)
            if config_version is not None
            else get_effective_config(session, scope, key)
        )
        return as_response(row)


config_client = ConfigClient(fetch=_fetch_from_db)
