"""ADR-003 — the one interface every service's publishers and workers
import. `services/*/publishers/*.py` and `services/*/workers/*.py` must
depend on `QueuePort`, never on a broker's native client — a
`grep -rl "^import redis\\b"` or `"^import nats\\b"` outside
`libs/queue/src/landqueue/drivers/` is a review finding.

Both drivers behind this port give: consumer groups, per-message ack,
redelivery of an un-acked message, and replay from a recorded sequence
position. A worker must not assume anything stronger (exactly-once,
cross-consumer ordering) than "at-least-once, per consumer group" — see
`docs/adr/ADR-003-queue-port.md` Consequences.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QueueMessage:
    """One delivered message. `sequence_id` is the broker-assigned position
    (Redis Stream ID / NATS sequence) — opaque to callers except as an
    argument to `replay_from`. `delivery_count` lets a handler notice a
    redelivery (e.g. to log a retry) without the port prescribing a
    dead-letter policy; DLQ handling is Phase 5 (§5, Phase 5 gate)."""

    sequence_id: str
    data: dict[str, Any]
    delivery_count: int


class QueuePort(abc.ABC):
    """Abstract broker port. `queue` names one of `contracts/asyncapi/*.yaml`
    (e.g. "triage-queue"); `group` is a consumer group name, conventionally
    the consuming service (e.g. "extraction-text-lane")."""

    @abc.abstractmethod
    def publish(self, queue: str, message: dict[str, Any]) -> str:
        """Publish `message` (already envelope-shaped —
        `observability.envelope.emit()` output) to `queue`. Returns the
        broker-assigned sequence id. Does not require the queue or
        consumer group to exist first (`ensure_group` creates it lazily on
        first `consume`, matching Streams'/JetStream's own semantics)."""

    @abc.abstractmethod
    def ensure_group(self, queue: str, group: str, *, start: str = "$") -> None:
        """Idempotently create `group` on `queue` if it doesn't exist yet.
        `start="$"` (the default for a direct call) means "new messages
        only, from now." `consume()` does **not** use this default for a
        first-time group — it creates a brand-new group at `start="0"`
        (the full backlog), because a group that has processed nothing yet
        starting at "now" would silently drop every message published
        before the worker came online. Call `ensure_group(..., start="$")`
        explicitly before any publish only if a consumer genuinely wants to
        skip pre-existing backlog. `replay_from` always uses `start="0"`."""

    @abc.abstractmethod
    def consume(
        self,
        queue: str,
        group: str,
        consumer_name: str,
        *,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[QueueMessage]:
        """Read up to `count` undelivered (or, if none, this consumer's own
        previously-delivered-but-unacked) messages, blocking up to
        `block_ms`. Returns an empty list on timeout — callers loop."""

    @abc.abstractmethod
    def ack(self, queue: str, group: str, sequence_id: str) -> None:
        """Acknowledge successful handling. Must only be called after the
        handler completes without raising — an un-acked message is
        redelivered to the group on the next `consume` (at-least-once)."""

    @abc.abstractmethod
    def replay_from(self, queue: str, group: str, sequence_id: str) -> None:
        """Reset `group`'s read position on `queue` to `sequence_id`
        (exclusive — the next `consume` returns the message *after* this
        one), so a group can be replayed from a known point. Used for
        FR-TRI-09 replay-reproducibility testing and for recovering a
        group that fell irrecoverably behind. `sequence_id="0"` replays
        the entire retained history."""
