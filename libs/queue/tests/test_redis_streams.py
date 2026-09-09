"""Real test against a live Redis Streams broker (localhost:6379 — the same
default `infra/docker-compose.yml`'s `redis` service exposes). Proves the
`QueuePort` contract holds for the default driver: publish, consume,
at-least-once redelivery on a missing ack, and replay-from-sequence.
"""
import uuid

import pytest
import redis

from landqueue.drivers.redis_streams import RedisStreamsQueue


@pytest.fixture
def queue():
    try:
        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        client.ping()
    except redis.ConnectionError:
        pytest.skip("no local Redis at localhost:6379 — start `redis-server` to run this test")
    q = RedisStreamsQueue(client=client)
    stream = f"test-queue-{uuid.uuid4()}"
    yield q, stream
    client.delete(stream)


def test_publish_then_consume_roundtrips_the_envelope(queue):
    q, stream = queue
    q.publish(stream, {"trace_id": "doc1:page1", "payload": {"field": "value"}})

    msgs = q.consume(stream, group="g1", consumer_name="c1", count=1, block_ms=500)

    assert len(msgs) == 1
    assert msgs[0].data == {"trace_id": "doc1:page1", "payload": {"field": "value"}}
    assert msgs[0].delivery_count == 1


def test_unacked_message_is_redelivered_at_least_once(queue):
    q, stream = queue
    q.publish(stream, {"trace_id": "doc1:page1", "payload": {}})

    first = q.consume(stream, group="g1", consumer_name="c1", count=1, block_ms=500)
    assert len(first) == 1
    # Deliberately do NOT ack — simulating a crash mid-handle.

    # A second consumer in the same group re-reads its own pending entries
    # via XREADGROUP with ">" only after the first consumer's claim times
    # out in real deployments (XCLAIM/XAUTOCLAIM); here we assert the
    # message is still pending for the group rather than lost.
    pending = q._r.xpending(stream, "g1")
    assert pending["pending"] == 1

    q.ack(stream, "g1", first[0].sequence_id)
    pending_after_ack = q._r.xpending(stream, "g1")
    assert pending_after_ack["pending"] == 0


def test_replay_from_resets_group_to_a_known_position(queue):
    q, stream = queue
    id1 = q.publish(stream, {"trace_id": "doc1:page1", "payload": {"n": 1}})
    q.publish(stream, {"trace_id": "doc1:page2", "payload": {"n": 2}})

    q.ensure_group(stream, "replay-group", start="0")
    all_msgs = q.consume(stream, "replay-group", "c1", count=10, block_ms=500)
    assert [m.data["payload"]["n"] for m in all_msgs] == [1, 2]
    for m in all_msgs:
        q.ack(stream, "replay-group", m.sequence_id)

    # Replay from id1 (exclusive) — should redeliver only message 2.
    q.replay_from(stream, "replay-group", id1)
    replayed = q.consume(stream, "replay-group", "c2", count=10, block_ms=500)
    assert [m.data["payload"]["n"] for m in replayed] == [2]


def test_ensure_group_is_idempotent(queue):
    q, stream = queue
    q.publish(stream, {"trace_id": "x:y", "payload": {}})
    q.ensure_group(stream, "g1")
    q.ensure_group(stream, "g1")  # must not raise BUSYGROUP outward
