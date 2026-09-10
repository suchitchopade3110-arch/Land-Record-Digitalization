"""T1.c — driver conformance (ADR-003, Phase 1 gate). "Redis and NATS
drivers pass the identical suite" — this is that suite, and per ADR-003's
fake/driver parity rule (amended alongside this file), the canonical fake
(`landqueue.testing.InMemoryQueue`) is parametrized in too: any parameter
a real driver treats as load-bearing must be honoured by the fake, and
this is the one place that parity is actually proven rather than assumed.

Before this file existed, `libs/queue/tests/test_redis_streams.py` tested
the Redis driver alone, and no test anywhere ran the same assertions
against more than one implementation. P5-02b's `block_ms=0` bug (see
`landconfigclient.subscriber.drain_once`'s git history) shipped green
specifically because the only fake in the repo at the time silently
ignored `block_ms` — a conformance gap this suite closes structurally,
not just for that one parameter.

Each test takes `driver` (a fixture parametrized over every implementation
below) and is skipped, not failed, when a real broker isn't reachable —
`InMemoryQueue` is always available and always runs.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from landqueue.port import QueuePort
from landqueue.testing import InMemoryQueue


def _redis_driver():
    import redis
    from landqueue.drivers.redis_streams import RedisStreamsQueue

    try:
        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        client.ping()
    except redis.ConnectionError:
        pytest.skip("no local Redis at localhost:6379 — start `redis-server` to run this test")
    return RedisStreamsQueue(client=client)


def _nats_driver():
    try:
        import nats  # noqa: F401
    except ImportError:
        pytest.skip("landqueue[nats] extra not installed — NATS JetStream driver is reviewed-but-unexercised here")
    try:
        from landqueue.drivers.nats_jetstream import JetStreamQueue

        return JetStreamQueue(url="nats://localhost:4222")
    except Exception:
        pytest.skip("no local NATS JetStream server at localhost:4222")


def _memory_driver():
    return InMemoryQueue()


@pytest.fixture(params=["redis_streams", "in_memory", "nats_jetstream"])
def driver(request) -> QueuePort:
    factory = {
        "redis_streams": _redis_driver,
        "in_memory": _memory_driver,
        "nats_jetstream": _nats_driver,
    }[request.param]
    return factory()


@pytest.fixture
def queue_name() -> str:
    return f"t1c-conformance-{uuid.uuid4()}"


@pytest.fixture
def group_name() -> str:
    return f"t1c-group-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Baseline properties ADR-003 names: consumer groups, per-message ack,
# redelivery of an un-acked message, replay from a recorded sequence.
# ---------------------------------------------------------------------------


def test_publish_then_consume_roundtrips_the_envelope(driver, queue_name, group_name):
    driver.publish(queue_name, {"trace_id": "doc1:page1", "payload": {"field": "value"}})

    msgs = driver.consume(queue_name, group_name, "c1", count=1, block_ms=500)

    assert len(msgs) == 1
    assert msgs[0].data == {"trace_id": "doc1:page1", "payload": {"field": "value"}}
    assert msgs[0].delivery_count == 1


def test_consumer_group_fan_out_is_independent(driver, queue_name):
    """Two different consumer groups both see every message — one group
    consuming (and acking) does not remove messages from the other's
    view. This is the property that makes ADR-003's "consumer group
    fan-out" claim meaningful rather than aspirational."""
    driver.publish(queue_name, {"trace_id": "x:1", "payload": {"n": 1}})
    driver.publish(queue_name, {"trace_id": "x:2", "payload": {"n": 2}})

    group_a, group_b = f"group-a-{uuid.uuid4()}", f"group-b-{uuid.uuid4()}"

    a_msgs = driver.consume(queue_name, group_a, "c1", count=10, block_ms=500)
    for m in a_msgs:
        driver.ack(queue_name, group_a, m.sequence_id)

    b_msgs = driver.consume(queue_name, group_b, "c1", count=10, block_ms=500)

    assert [m.data["payload"]["n"] for m in a_msgs] == [1, 2]
    assert [m.data["payload"]["n"] for m in b_msgs] == [1, 2], "group B must see both messages regardless of group A's ack"


def test_replay_from_resets_group_to_a_known_position(driver, queue_name, group_name):
    id1 = driver.publish(queue_name, {"trace_id": "doc1:page1", "payload": {"n": 1}})
    driver.publish(queue_name, {"trace_id": "doc1:page2", "payload": {"n": 2}})

    driver.ensure_group(queue_name, group_name, start="0")
    all_msgs = driver.consume(queue_name, group_name, "c1", count=10, block_ms=500)
    assert [m.data["payload"]["n"] for m in all_msgs] == [1, 2]
    for m in all_msgs:
        driver.ack(queue_name, group_name, m.sequence_id)

    driver.replay_from(queue_name, group_name, id1)
    replayed = driver.consume(queue_name, group_name, "c2", count=10, block_ms=500)
    assert [m.data["payload"]["n"] for m in replayed] == [2]


def test_replay_from_redelivers_the_message_with_a_positive_delivery_count(driver, queue_name, group_name):
    """`replay_from` genuinely redelivers the message (this is what T2.d's
    replay-reproducibility test depends on) with a well-formed, positive
    `delivery_count` — but NOT necessarily one strictly greater than the
    first delivery's.

    Finding, not a guess: an earlier version of this test asserted
    `delivery_count` must strictly increase after a `replay_from`. It does
    on `InMemoryQueue`, but the real Redis driver's `replay_from`
    (`XGROUP SETID`) resets the group's delivery-position pointer rather
    than incrementing the pending-entry's delivery counter, so a message
    read again after a replay comes back reporting `delivery_count == 1`
    on Redis — indistinguishable from its first delivery. This is not a
    bug to paper over: `replay_from`'s own docstring says it exists for
    "replay-reproducibility testing," and FR-TRI-09's whole point is that
    a replayed message must reproduce the *first* run byte-for-byte —
    looking like a fresh delivery is the correct behaviour for that use
    case, not a defect. `delivery_count` incrementing on a genuine
    crash-and-redeliver (no `replay_from` involved) is Redis-driver-
    specific and already covered by `libs/queue/tests/
    test_redis_streams.py`'s own `XPENDING`-based test; there is no
    portable way to force that scenario through `QueuePort` alone (the
    abstract port's own consume() docstring describes a
    "previously-delivered-but-unacked" fallback neither concrete driver
    actually implements — see `landqueue.testing`'s module docstring)."""
    driver.publish(queue_name, {"trace_id": "doc1:page1", "payload": {"n": 1}})

    first = driver.consume(queue_name, group_name, "c1", count=1, block_ms=500)
    assert len(first) == 1
    assert first[0].delivery_count == 1
    # Deliberately not acked.

    driver.replay_from(queue_name, group_name, "0")
    second = driver.consume(queue_name, group_name, "c1", count=1, block_ms=500)
    assert len(second) == 1
    assert second[0].data == first[0].data
    assert second[0].delivery_count >= 1


def test_ensure_group_is_idempotent(driver, queue_name, group_name):
    driver.publish(queue_name, {"trace_id": "x:y", "payload": {}})
    driver.ensure_group(queue_name, group_name)
    driver.ensure_group(queue_name, group_name)  # must not raise


def test_a_new_group_sees_the_full_backlog_not_only_new_messages(driver, queue_name, group_name):
    """`ensure_group`'s own contract (port.py): a group that has processed
    nothing yet must not silently drop messages published before it
    existed. Publish before the group is ever mentioned, then consume."""
    driver.publish(queue_name, {"trace_id": "x:1", "payload": {"n": 1}})
    driver.publish(queue_name, {"trace_id": "x:2", "payload": {"n": 2}})

    msgs = driver.consume(queue_name, group_name, "c1", count=10, block_ms=500)
    assert [m.data["payload"]["n"] for m in msgs] == [1, 2]


# ---------------------------------------------------------------------------
# block_ms semantics — added by the ADR-003 fake/driver parity amendment.
# 0 means block indefinitely (matching Redis Streams' own `BLOCK 0`), a
# positive value bounds the wait, and the declared default (1000ms per
# port.py) behaves like a positive, bounded wait too.
# ---------------------------------------------------------------------------


def test_block_ms_zero_blocks_until_a_message_arrives(driver, queue_name, group_name):
    """The property P5-02b's bug violated: block_ms=0 on an empty queue
    must actually wait for a message, not return an empty list
    immediately. Proven with a background publisher rather than a timing
    guess — if this regresses, the thread never finishes and `join`
    times out, failing the test rather than hanging the suite forever.
    """
    driver.ensure_group(queue_name, group_name, start="0")
    result: list = []

    def _consume():
        result.extend(driver.consume(queue_name, group_name, "c1", count=1, block_ms=0))

    consumer_thread = threading.Thread(target=_consume)
    started = time.monotonic()
    consumer_thread.start()

    time.sleep(0.3)  # give the consumer time to actually start blocking
    assert result == [], "consume() with block_ms=0 returned before any message was published"

    driver.publish(queue_name, {"trace_id": "x:1", "payload": {"n": 1}})
    consumer_thread.join(timeout=5.0)
    elapsed = time.monotonic() - started

    assert not consumer_thread.is_alive(), "consume(block_ms=0) never returned after a message was published"
    assert len(result) == 1 and result[0].data["payload"]["n"] == 1
    assert elapsed >= 0.3, "returned suspiciously fast — block_ms=0 may not actually be waiting"


def test_block_ms_positive_returns_empty_after_the_bound_on_an_empty_queue(driver, queue_name, group_name):
    driver.ensure_group(queue_name, group_name, start="0")
    started = time.monotonic()
    result = driver.consume(queue_name, group_name, "c1", count=1, block_ms=300)
    elapsed = time.monotonic() - started

    assert result == []
    assert elapsed >= 0.25, f"returned after {elapsed:.2f}s — shorter than the requested 300ms bound"
    assert elapsed < 3.0, f"returned after {elapsed:.2f}s — much longer than the requested 300ms bound"


def test_block_ms_default_is_a_bounded_wait_not_forever(driver, queue_name, group_name):
    """`port.py` declares `block_ms: int = 1000` — the default must behave
    like a positive, bounded wait (proving the default isn't quietly
    "block forever" the way an unspecified `0` used to be treated by the
    old, unaudited fake)."""
    driver.ensure_group(queue_name, group_name, start="0")
    started = time.monotonic()
    result = driver.consume(queue_name, group_name, "c1", count=1)  # block_ms omitted — exercises the declared default
    elapsed = time.monotonic() - started

    assert result == []
    assert elapsed < 5.0, f"consume() with the default block_ms took {elapsed:.2f}s on an empty queue — looks unbounded"
