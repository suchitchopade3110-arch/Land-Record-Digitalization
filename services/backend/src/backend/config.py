"""Reads config from the Config Service (M13) at startup + on invalidation
event — never polls only. TODO: FR-CFG-01/02/04.

Wraps observability.ConfigClient (shared cache-by-config_version behavior)
around this service's own Config Service implementation (it IS the owner —
other services call it over HTTP; this module is the in-process read path
used by backend's own workers).
"""
from __future__ import annotations

from observability import ConfigClient


def _fetch_from_db(scope: str, key: str) -> dict:
    """TODO: FR-CFG-01 — read the active ConfigVersion row for (scope, key)
    from Postgres. Placeholder until infra/migrations defines the table."""
    raise NotImplementedError("TODO: FR-CFG-01 — query ConfigVersion table")


config_client = ConfigClient(fetch=_fetch_from_db)
