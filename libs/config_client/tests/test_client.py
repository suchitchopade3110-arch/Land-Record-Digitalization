"""P5-04 test suite. Every test uses a fake `fetch` counting its own calls
so "hit the cache, not the network" is asserted directly, never inferred
from timing.
"""
from __future__ import annotations

import pytest
from landconfigclient.client import ConfigClient
from landenvelope import LiveResolutionInsidePinnedScope, pinned_scope


class FakeClock:
    """A controllable clock — `ttl_seconds` tests must not depend on wall
    time actually elapsing."""

    def __init__(self, start: float = 0.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeConfigService:
    """Stands in for `GET /config/{scope}/{key}[?config_version=]` — a
    versioned store the test can bump, with a call counter per
    (scope, key, config_version) so "hit the cache" is a call-count
    assertion, not a guess."""

    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []
        self._current: dict[tuple[str, str], dict] = {}
        self._by_version: dict[tuple[str, str, str], dict] = {}

    def set_current(self, scope: str, key: str, config_version: str, value: dict, effective_from: str) -> None:
        row = {"key": key, "value": value, "config_version": config_version, "effective_from": effective_from}
        self._current[(scope, key)] = row
        self._by_version[(scope, key, config_version)] = row

    def fetch(self, scope: str, key: str, config_version: str | None) -> dict:
        self.calls.append((scope, key, config_version))
        if config_version is not None:
            return dict(self._by_version[(scope, key, config_version)])
        return dict(self._current[(scope, key)])


@pytest.fixture
def service() -> FakeConfigService:
    svc = FakeConfigService()
    svc.set_current(
        "district", "unit_table.sitapur", "v1",
        {"bigha_to_sqm": 1000.0}, "2026-01-01T00:00:00+00:00",
    )
    return svc


# ---------------------------------------------------------------------------
# 1. A repeat read for the same (scope, key) hits the cache, not the network.
# ---------------------------------------------------------------------------


def test_repeat_unpinned_read_hits_cache_not_network(service):
    client = ConfigClient(fetch=service.fetch)

    first = client.get("district", "unit_table.sitapur")
    second = client.get("district", "unit_table.sitapur")

    assert first == second
    assert len(service.calls) == 1, f"expected exactly one fetch, got {service.calls}"


def test_repeat_pinned_read_hits_cache_not_network(service):
    client = ConfigClient(fetch=service.fetch)

    first = client.get("district", "unit_table.sitapur", config_version="v1")
    second = client.get("district", "unit_table.sitapur", config_version="v1")

    assert first == second
    assert len(service.calls) == 1


# ---------------------------------------------------------------------------
# 2. A version-change event invalidates the entry; the next read returns
#    the new value and the new config_version.
# ---------------------------------------------------------------------------


def test_version_change_event_invalidates_and_next_read_sees_new_version(service):
    client = ConfigClient(fetch=service.fetch)

    before = client.get("district", "unit_table.sitapur")
    assert before["config_version"] == "v1"
    assert len(service.calls) == 1

    service.set_current(
        "district", "unit_table.sitapur", "v2",
        {"bigha_to_sqm": 1008.0}, "2026-02-01T00:00:00+00:00",
    )
    # Still cached — the event hasn't arrived yet.
    assert client.get("district", "unit_table.sitapur")["config_version"] == "v1"
    assert len(service.calls) == 1

    client.handle_version_change_event({"scope": "district", "key": "unit_table.sitapur", "config_version": "v2"})

    after = client.get("district", "unit_table.sitapur")
    assert after["config_version"] == "v2"
    assert after["value"] == {"bigha_to_sqm": 1008.0}
    assert len(service.calls) == 2


# ---------------------------------------------------------------------------
# 3. Timer expiry alone also refreshes, but the event path works with the
#    timer disabled.
# ---------------------------------------------------------------------------


def test_ttl_expiry_alone_also_refreshes(service):
    clock = FakeClock()
    client = ConfigClient(fetch=service.fetch, ttl_seconds=60.0, clock=clock)

    client.get("district", "unit_table.sitapur")
    assert len(service.calls) == 1

    service.set_current("district", "unit_table.sitapur", "v2", {"bigha_to_sqm": 1008.0}, "2026-02-01T00:00:00+00:00")
    clock.advance(61.0)  # past the TTL, no event delivered

    after = client.get("district", "unit_table.sitapur")
    assert after["config_version"] == "v2"
    assert len(service.calls) == 2


def test_event_path_invalidates_correctly_with_timer_disabled():
    """The contract is explicit that polling must not be the *sole*
    invalidation mechanism — proved here by disabling the timer
    (`ttl_seconds=None`, i.e. an entry never expires on its own) and
    showing the event path still invalidates and refreshes correctly."""
    service = FakeConfigService()
    service.set_current("global", "error_policy", "v1", {"max_error_rate": 0.02}, "2026-01-01T00:00:00+00:00")
    client = ConfigClient(fetch=service.fetch, ttl_seconds=None)

    client.get("global", "error_policy")
    client.get("global", "error_policy")
    client.get("global", "error_policy")
    assert len(service.calls) == 1, "TTL disabled should mean a cached entry never expires on its own"

    service.set_current("global", "error_policy", "v2", {"max_error_rate": 0.01}, "2026-03-01T00:00:00+00:00")
    client.handle_version_change_event({"scope": "global", "key": "error_policy", "config_version": "v2"})

    after = client.get("global", "error_policy")
    assert after["config_version"] == "v2"
    assert after["value"] == {"max_error_rate": 0.01}
    assert len(service.calls) == 2


# ---------------------------------------------------------------------------
# 4. Cache key is the (scope, key, config_version) triple. Two versions of
#    the same key coexist without evicting each other.
# ---------------------------------------------------------------------------


def test_two_pinned_versions_of_the_same_key_coexist(service):
    service.set_current("district", "unit_table.sitapur", "v2", {"bigha_to_sqm": 1008.0}, "2026-02-01T00:00:00+00:00")
    client = ConfigClient(fetch=service.fetch)

    v1 = client.get("district", "unit_table.sitapur", config_version="v1")
    v2 = client.get("district", "unit_table.sitapur", config_version="v2")
    assert v1["value"] == {"bigha_to_sqm": 1000.0}
    assert v2["value"] == {"bigha_to_sqm": 1008.0}
    assert len(service.calls) == 2

    # Reading v1 again after v2 was fetched doesn't re-fetch v1 — v2's
    # cache entry did not evict v1's.
    again_v1 = client.get("district", "unit_table.sitapur", config_version="v1")
    assert again_v1["value"] == {"bigha_to_sqm": 1000.0}
    assert len(service.calls) == 2


def test_effective_and_pinned_caches_are_independent(service):
    """An unpinned read warms the pinned cache for the version it landed
    on (no wasted round trip later), but the two forms remain separately
    keyed — invalidating the effective entry must not touch the pinned one."""
    client = ConfigClient(fetch=service.fetch)

    client.get("district", "unit_table.sitapur")  # unpinned -> v1, warms pinned[v1] too
    assert len(service.calls) == 1

    pinned = client.get("district", "unit_table.sitapur", config_version="v1")
    assert pinned["config_version"] == "v1"
    assert len(service.calls) == 1, "pinned read of the version just seen as current should be free"

    client.invalidate("district", "unit_table.sitapur")
    # Pinned cache survives an effective-cache invalidation.
    client.get("district", "unit_table.sitapur", config_version="v1")
    assert len(service.calls) == 1


# ---------------------------------------------------------------------------
# 5. Reading while a pinned WorkEnvelope is in scope raises (invariant 3 /
#    FR-TRI-09) — reuses landenvelope.guard_resolve_active.
# ---------------------------------------------------------------------------


def test_unpinned_read_inside_pinned_envelope_scope_raises(service):
    client = ConfigClient(fetch=service.fetch)

    with pinned_scope("envelope-123"), pytest.raises(LiveResolutionInsidePinnedScope):
        client.get("district", "unit_table.sitapur")

    # No fetch happened — the guard fires before any I/O.
    assert service.calls == []


def test_pinned_read_inside_pinned_envelope_scope_is_allowed(service):
    """A pinned read is exactly how code inside a pinned scope is meant to
    read config — it must never be blocked by the same guard that blocks
    "resolve current"."""
    client = ConfigClient(fetch=service.fetch)

    with pinned_scope("envelope-123"):
        pinned = client.get("district", "unit_table.sitapur", config_version="v1")

    assert pinned["config_version"] == "v1"


def test_unpinned_read_allowed_again_once_scope_exits(service):
    client = ConfigClient(fetch=service.fetch)

    with pinned_scope("envelope-123"), pytest.raises(LiveResolutionInsidePinnedScope):
        client.get("district", "unit_table.sitapur")

    # Outside the scope, the same call succeeds normally.
    value = client.get("district", "unit_table.sitapur")
    assert value["config_version"] == "v1"
