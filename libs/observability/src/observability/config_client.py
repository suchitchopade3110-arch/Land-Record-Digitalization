"""Shared Config Service client — one code path for caching + invalidation.

TODO: FR-CFG-02, API-Contracts-and-Interfaces.md §4.1. Callers (Shree, Shruthi,
Tharun, and Suchit's own workers) must cache on `config_version` as the cache
key and invalidate on version change — never a bare timer poll, or a config
change (FR-CFG-04) won't propagate until the next poll and reprocessing
accounting gets confused about which version was "active" when.

TODO: wire the actual HTTP call to GET /config/{scope}/{key} (Suchit's
services/backend) once the gateway/route_registry.yaml route is implemented.
"""
from __future__ import annotations

from typing import Any, Callable


class ConfigClient:
    """Caches config values keyed by (scope, key) -> (config_version, value).

    A call to `get()` with a stale locally-cached config_version re-fetches;
    an unchanged config_version is served from cache. This is the "cache on
    config_version, invalidate on change" rule applied once, here, instead of
    once per service.
    """

    def __init__(self, fetch: Callable[[str, str], dict[str, Any]]):
        """`fetch(scope, key)` -> {"key", "value", "config_version", "effective_from"},
        i.e. the shape of GET /config/{scope}/{key} (contracts/openapi/config-service.suchit.yaml).
        """
        self._fetch = fetch
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}

    def get(self, scope: str, key: str) -> dict[str, Any]:
        fresh = self._fetch(scope, key)
        cached = self._cache.get((scope, key))
        if cached is None or cached["config_version"] != fresh["config_version"]:
            self._cache[(scope, key)] = fresh
        return self._cache[(scope, key)]
