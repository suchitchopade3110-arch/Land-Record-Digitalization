"""Proves `drain_once` is the real event-delivery mechanism `client.py`'s
own tests assume — a `QueuePort` conformance-shaped fake (ADR-003), not a
live broker, matching how `libs/outbox/tests/test_relay.py` tests its
relay against the port rather than a real Redis/NATS.
"""
from __future__ import annotations

from landconfigclient.client import ConfigClient, _EffectiveCacheEntry
from landconfigclient.subscriber import QUEUE_NAME, drain_once
from landqueue.port import QueueMessage, QueuePort


class FakeQueue(QueuePort):
    """In-memory `QueuePort` — just enough of the port for `drain_once`:
    publish appends, consume drains up to `count` unacked messages, ack
    removes them from the "in flight" set."""

    def __init__(self):
        self._pending: list[QueueMessage] = []
        self._next_seq = 0

    def publish(self, queue: str, message: dict) -> str:
        self._next_seq += 1
        seq = str(self._next_seq)
        self._pending.append(QueueMessage(sequence_id=seq, data=message, delivery_count=1))
        return seq

    def ensure_group(self, queue: str, group: str, *, start: str = "$") -> None:
        pass

    def consume(self, queue: str, group: str, consumer_name: str, *, count: int = 1, block_ms: int = 1000):
        batch = self._pending[:count]
        return batch

    def ack(self, queue: str, group: str, sequence_id: str) -> None:
        self._pending = [m for m in self._pending if m.sequence_id != sequence_id]

    def replay_from(self, queue: str, group: str, sequence_id: str) -> None:
        raise NotImplementedError


def test_drain_once_applies_pending_invalidations_and_acks_them():
    queue = FakeQueue()

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
    assert queue._pending == []  # acked
    refreshed = client.get("district", "unit_table.sitapur")
    assert refreshed["config_version"] == "v2"


def test_drain_once_with_nothing_pending_is_a_noop():
    queue = FakeQueue()
    client = ConfigClient(fetch=lambda scope, key, cv: {})

    assert drain_once(client, queue, group="g", consumer_name="c") == 0


def test_drain_once_default_does_not_block_forever_against_a_real_broker():
    """P5-02b regression: the old default (`block_ms=0`) meant "block
    indefinitely" against the real Redis Streams driver (`XREADGROUP ...
    BLOCK 0`), the opposite of this function's old "non-blocking" claim —
    `FakeQueue` above never caught it because it ignores `block_ms`
    entirely. Skipped if no local Redis; real broker only, since the
    whole point is proving the real driver's behaviour, not a fake's."""
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
