"""Proves `drain_once` is the real event-delivery mechanism `client.py`'s
own tests assume — a `QueuePort` conformance-shaped fake (ADR-003), not a
live broker, matching how `libs/outbox/tests/test_relay.py` tests its
relay against the port rather than a real Redis/NATS.

Uses `landqueue.testing.InMemoryQueue`, the one canonical `QueuePort` fake
this repo maintains (see that module's docstring) — not a private local
fake. This file used to define its own `FakeQueue`, which silently
ignored `block_ms` entirely; that gap is exactly how P5-02b's
`block_ms=0` bug shipped green, and it's why a single, ADR-003-audited
fake now exists instead of one per test file. `tests/queue/
test_conformance.py` (T1.c) is what proves `InMemoryQueue` itself behaves
like the real drivers — this file just uses it.
"""
from __future__ import annotations

from landconfigclient.client import ConfigClient, _EffectiveCacheEntry
from landconfigclient.subscriber import QUEUE_NAME, drain_once
from landqueue.testing import InMemoryQueue


def test_drain_once_applies_pending_invalidations_and_acks_them():
    queue = InMemoryQueue()

    def fetch(scope, key, config_version):
        return {"key": key, "value": {"n": 2}, "config_version": "v2", "effective_from": "2026-02-01T00:00:00+00:00"}

    client = ConfigClient(fetch=fetch)
    # Seed a cache entry directly (bypassing a fetch) so we can observe it
    # get invalidated by the drained event, not by coincidence.
    client._effective_cache[("district", "unit_table.sitapur")] = _EffectiveCacheEntry(
        value={"key": "unit_table.sitapur", "value": {"n": 1}, "config_version": "v1", "effective_from": "x"},
        cached_at=0.0,
    )

    queue.publish(QUEUE_NAME, {"scope": "district", "key": "unit_table.sitapur", "config_version": "v2"})

    processed = drain_once(client, queue, group="shree-extraction", consumer_name="worker-1")

    assert processed == 1
    assert queue.pending_count(QUEUE_NAME, "shree-extraction") == 0  # acked
    refreshed = client.get("district", "unit_table.sitapur")
    assert refreshed["config_version"] == "v2"


def test_drain_once_with_nothing_pending_is_a_noop():
    queue = InMemoryQueue()
    client = ConfigClient(fetch=lambda scope, key, cv: {})

    assert drain_once(client, queue, group="g", consumer_name="c") == 0


def test_drain_once_default_does_not_block_forever_against_a_real_broker():
    """P5-02b regression: the old default (`block_ms=0`) meant "block
    indefinitely" against the real Redis Streams driver (`XREADGROUP ...
    BLOCK 0`), the opposite of this function's old "non-blocking" claim —
    the fake this file used before never caught it because it ignored
    `block_ms` entirely (see this module's docstring). `InMemoryQueue` now
    honours `block_ms` too (proven in `tests/queue/test_conformance.py`),
    but this specific regression is worth proving against the real broker
    directly, not just the fake — skipped if no local Redis."""
    import os

    import pytest
    import redis as redis_lib
    from landqueue.drivers.redis_streams import RedisStreamsQueue

    url = os.environ.get("QUEUE_URL", "redis://localhost:6379/0")
    try:
        redis_lib.Redis.from_url(url, decode_responses=True).ping()
    except redis_lib.RedisError:
        pytest.skip(f"no local Redis at {url}")

    queue = RedisStreamsQueue(url=url)
    client = ConfigClient(fetch=lambda scope, key, cv: {})

    # No messages pending for a brand-new, uniquely-named group — if this
    # call blocks for more than a few seconds, the regression is back.
    import time

    started = time.monotonic()
    processed = drain_once(client, queue, group=f"regression-test-group-{time.monotonic_ns()}", consumer_name="c")
    elapsed = time.monotonic() - started

    assert processed == 0
    assert elapsed < 5.0, f"drain_once took {elapsed:.1f}s with nothing pending — block_ms default regressed"
