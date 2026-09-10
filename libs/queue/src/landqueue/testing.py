"""ADR-003's fake/driver parity rule, in code: `InMemoryQueue` is the one
canonical `QueuePort` fake this repo uses in tests that don't need a real
broker. Before this module existed, the only fake anywhere in this
repository was a private `FakeQueue` inside
`libs/config_client/tests/test_subscriber.py`, and it silently ignored
`block_ms` entirely (`consume()` never waited, regardless of the
argument) — which is exactly how P5-02b's `block_ms=0` bug (see
`landconfigclient.subscriber.drain_once`'s git history) shipped green:
the fake never exercised the one behaviour that mattered.

Not for production use — this is test-only utility code shipped inside
`landqueue` so every test in this repo (and in `libs/config_client`,
which cannot depend on `libs/queue`'s *tests* directory) can import one
implementation instead of each hand-rolling its own with its own gaps.
`tests/queue/test_conformance.py` (T1.c) is what proves this fake
actually behaves like the real drivers, parametrizing the identical suite
over both — see that module and `docs/adr/ADR-003-queue-port.md`'s
"fake/driver parity" rule for why this is asserted, not assumed.

Faithfulness, and where it stops:
- Separate storage per `queue` name (a real broker never mixes two
  streams together — the old fake had exactly one shared list regardless
  of which `queue` was asked for).
- Separate cursor and pending-set per `(queue, group)` (real consumer
  groups fan out independently — the old fake had one shared pending
  list, so acking in one group would have wrongly cleared another
  group's view of the same message).
- `delivery_count` actually increments per `(queue, group, message)` on
  each delivery (the old fake hardcoded `1` forever).
- `block_ms` is real: `0` blocks until a message arrives (matching
  `RedisStreamsQueue`'s `XREADGROUP ... BLOCK 0` semantics — Redis's own
  "block indefinitely," not "don't block"), a positive value blocks up
  to that many milliseconds, and it returns `[]` on timeout either way.
- `consume()` only ever returns genuinely new (never-delivered-to-this-
  group) messages — matching `RedisStreamsQueue`'s own deliberate
  simplification (its own docstring explains why), not the abstract
  port's slightly broader "or, if none, this consumer's own
  previously-delivered-but-unacked" language. Parity is judged against
  what the real drivers actually do, not against the abstract docstring's
  aspirational phrasing.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from landqueue.port import QueueMessage, QueuePort


class InMemoryQueue(QueuePort):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._streams: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._next_seq = 0
        # (queue, group) -> index into self._streams[queue] of the next
        # unread entry for that group.
        self._cursors: dict[tuple[str, str], int] = {}
        # (queue, group) -> {sequence_id: QueueMessage}, messages delivered
        # to this group and not yet acked.
        self._pending: dict[tuple[str, str], dict[str, QueueMessage]] = {}
        # (queue, group, sequence_id) -> times delivered to this group.
        self._delivery_counts: dict[tuple[str, str, str], int] = {}

    def publish(self, queue: str, message: dict[str, Any]) -> str:
        with self._not_empty:
            self._next_seq += 1
            seq_id = str(self._next_seq)
            self._streams.setdefault(queue, []).append((seq_id, message))
            self._not_empty.notify_all()
        return seq_id

    def ensure_group(self, queue: str, group: str, *, start: str = "$") -> None:
        key = (queue, group)
        with self._lock:
            if key in self._cursors:
                return  # idempotent — matches the real drivers' BUSYGROUP no-op
            stream_len = len(self._streams.get(queue, []))
            self._cursors[key] = 0 if start == "0" else stream_len
            self._pending.setdefault(key, {})

    def consume(
        self,
        queue: str,
        group: str,
        consumer_name: str,
        *,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[QueueMessage]:
        # A brand-new group always starts at the full backlog ("0"), same
        # reasoning as RedisStreamsQueue.consume's own comment: a group
        # that has processed nothing yet must not silently drop history.
        self.ensure_group(queue, group, start="0")
        key = (queue, group)
        deadline = None if block_ms == 0 else (time.monotonic() + block_ms / 1000.0)

        with self._not_empty:
            while True:
                stream = self._streams.get(queue, [])
                cursor = self._cursors[key]
                batch = stream[cursor : cursor + count]
                if batch:
                    self._cursors[key] = cursor + len(batch)
                    messages: list[QueueMessage] = []
                    for seq_id, data in batch:
                        dcount_key = (queue, group, seq_id)
                        delivery_count = self._delivery_counts.get(dcount_key, 0) + 1
                        self._delivery_counts[dcount_key] = delivery_count
                        msg = QueueMessage(sequence_id=seq_id, data=data, delivery_count=delivery_count)
                        self._pending[key][seq_id] = msg
                        messages.append(msg)
                    return messages

                # Nothing new yet. block_ms=0 means "block indefinitely"
                # (matching the real Redis driver's `BLOCK 0`), not
                # "don't block" — this is the exact distinction the old
                # fake erased by ignoring block_ms altogether.
                if deadline is None:
                    self._not_empty.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._not_empty.wait(timeout=remaining)

    def ack(self, queue: str, group: str, sequence_id: str) -> None:
        with self._lock:
            self._pending.get((queue, group), {}).pop(sequence_id, None)

    def replay_from(self, queue: str, group: str, sequence_id: str) -> None:
        key = (queue, group)
        with self._lock:
            stream = self._streams.get(queue, [])
            index = next((i for i, (sid, _) in enumerate(stream) if sid == sequence_id), None)
            self._cursors[key] = 0 if index is None else index + 1

    def pending_count(self, queue: str, group: str) -> int:
        """Test-only helper (no `QueuePort` equivalent exists) — the
        in-memory analogue of `RedisStreamsQueue`'s own tests reading
        `XPENDING` directly to check a group's unacked count."""
        with self._lock:
            return len(self._pending.get((queue, group), {}))
