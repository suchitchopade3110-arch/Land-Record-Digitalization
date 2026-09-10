"""P5-04 — the shared Config Service client (contract §4.1, FR-CFG-02).

Every reader of `GET /config/{scope}/{key}` — Shree's, Shruthi's and
Tharun's services, and `services/backend`'s own workers (the triage
router, the impact-preview job) — imports this instead of hand-rolling a
cache. It supersedes the interim `observability.ConfigClient` stub, which
only diffed `config_version` on every call and never met §4.1's "never
poll on a timer as the sole invalidation mechanism" bar; that stub is left
in place for now so services still importing it keep building, but new
code should use this package instead (see the P5-04 phase report).

Two read forms, matching the two forms `api/config_service.py`'s own
route exposes over HTTP:

- **Unpinned** — `get(scope, key)`, "what is effective now". Cached under
  `(scope, key)` only, since there is exactly one current answer at a
  time. Invalidated two ways: a version-change event
  (`handle_version_change_event` / `subscriber.drain_once`, the actual
  mechanism), and, as a backstop only, `ttl_seconds` — the event path must
  keep working with the TTL disabled entirely. Per CLAUDE.md invariant 3 /
  FR-CFG-02, this form refuses to run at all while a `WorkEnvelope` is
  pinned for the message currently being handled — nothing downstream of
  triage may resolve "current" config — enforced by reusing
  `landenvelope.guard_resolve_active`, not a second copy of that check.

- **Pinned** — `get(scope, key, config_version=vN)`, that exact,
  immutable row, forever. Cached under the full `(scope, key,
  config_version)` triple, so two versions of the same key coexist
  without evicting each other — correct, because a pinned `ConfigVersion`
  row never changes (P5-01) and is therefore safe to cache forever. Never
  guarded: reading a specific pinned version is exactly how code inside a
  pinned scope is supposed to read config.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from landenvelope import guard_resolve_active

FetchFn = Callable[[str, str, "str | None"], dict[str, Any]]


@dataclass
class _EffectiveCacheEntry:
    value: dict[str, Any]
    cached_at: float


class ConfigClient:
    """`fetch(scope, key, config_version)` -> the §4.1 response shape
    (`key, value, config_version, effective_from`) — `config_version` is
    `None` for an unpinned ("effective now") fetch, or a specific id for a
    pinned one. This is an in-process client: it does not decide HTTP vs.
    direct-DB-call, `fetch` does (a service-local `fetch` reads
    `services/backend`'s own tables in-process; every other service's
    `fetch` calls `GET /config/{scope}/{key}` over HTTP).
    """

    def __init__(
        self,
        fetch: FetchFn,
        *,
        ttl_seconds: float | None = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._pinned_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._effective_cache: dict[tuple[str, str], _EffectiveCacheEntry] = {}

    def get(self, scope: str, key: str, config_version: str | None = None) -> dict[str, Any]:
        if config_version is not None:
            return self._get_pinned(scope, key, config_version)
        return self._get_effective(scope, key)

    def _get_pinned(self, scope: str, key: str, config_version: str) -> dict[str, Any]:
        cache_key = (scope, key, config_version)
        cached = self._pinned_cache.get(cache_key)
        if cached is not None:
            return cached
        value = self._fetch(scope, key, config_version)
        self._pinned_cache[cache_key] = value
        return value

    def _get_effective(self, scope: str, key: str) -> dict[str, Any]:
        # Invariant 3 / FR-CFG-02: refuse to resolve "current" while a
        # WorkEnvelope is pinned for the message in scope. A pinned read
        # (above) is exempt on purpose — it isn't resolving "current",
        # it's reading the exact version the envelope already named.
        guard_resolve_active(f"config:{scope}.{key}")

        cache_key = (scope, key)
        entry = self._effective_cache.get(cache_key)
        if entry is not None and not self._expired(entry):
            return entry.value

        value = self._fetch(scope, key, None)
        self._effective_cache[cache_key] = _EffectiveCacheEntry(value=value, cached_at=self._clock())
        # An unpinned read that lands on some config_version also warms
        # the pinned cache for that exact value — a later pinned read of
        # the version the client just saw as "current" costs nothing extra.
        self._pinned_cache.setdefault((scope, key, value["config_version"]), value)
        return value

    def _expired(self, entry: _EffectiveCacheEntry) -> bool:
        if self._ttl_seconds is None:
            return False
        return (self._clock() - entry.cached_at) >= self._ttl_seconds

    def invalidate(self, scope: str, key: str) -> None:
        """Drop the cached *effective* value for `(scope, key)`, forcing
        the next unpinned `get()` to re-fetch. This is what a
        version-change event does (`handle_version_change_event`) — never
        call this from a timer; `ttl_seconds` is the timer backstop, kept
        deliberately separate so the event path is provably not the timer
        in disguise.

        Pinned entries are never invalidated — they're immutable rows, so
        there is nothing to invalidate them to.
        """
        self._effective_cache.pop((scope, key), None)

    def handle_version_change_event(self, payload: dict[str, Any]) -> None:
        """Apply one decoded invalidation message —
        `{"scope": ..., "key": ..., "config_version": ...}`, the payload
        shape a `ConfigVersion` write publishes (see
        `backend.domain.config_versions.build_version_change_event`) — by
        invalidating the affected `(scope, key)` entry. `config_version`
        is carried for observability/logging only; this client always
        re-fetches "effective now" on the next `get()` rather than trusting
        the event's own value, so a redelivered or out-of-order event is
        harmless.
        """
        self.invalidate(payload["scope"], payload["key"])
