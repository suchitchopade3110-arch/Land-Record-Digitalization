"""The relay side of ADR-005. Polls `outbox_message` for undispatched
rows, publishes each through `landqueue` (ADR-003), and marks it
dispatched — using the queue's own delivery guarantee, not a shared
transaction with Postgres. Safe to crash and resume: an
already-published-but-not-yet-marked-dispatched row is republished on
restart, which is why every consumer downstream must already be
idempotent (§04 of the PRD) — this is not a new requirement the relay
introduces.
"""
from __future__ import annotations

from datetime import datetime, timezone

from landqueue.port import QueuePort
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from landoutbox.models import OutboxMessage


class Relay:
    def __init__(self, session_factory: sessionmaker[Session], queue: QueuePort):
        self._session_factory = session_factory
        self._queue = queue

    def drain_once(self, *, batch_size: int = 100) -> int:
        """Publish up to `batch_size` undispatched messages, oldest first.
        Returns the count actually dispatched.

        The row lock from `FOR UPDATE SKIP LOCKED` is held for the whole
        batch — through every publish call, not just the SELECT — and
        released only on commit at the end. Releasing it earlier (so a
        publish's network latency isn't spent holding a lock) leaves a
        window between "lock released" and "row marked dispatched" where
        a second relay instance's SELECT sees the same rows as still
        `dispatched=False` and unlocked, and republishes them itself —
        duplicate broker publishes from concurrent relay loops, not just
        the already-idempotent-downstream redelivery this module's
        docstring accepts. Holding the lock for the batch's duration is
        the tradeoff that closes that race without adding a claim/lease
        column; a crash mid-batch still leaves every unmarked row
        `dispatched=False` for the next run to pick up (safe, since
        downstream consumers are already required to be idempotent)."""
        dispatched_count = 0
        with self._session_factory() as session:
            stmt = (
                select(OutboxMessage)
                .where(OutboxMessage.dispatched.is_(False))
                .order_by(OutboxMessage.created_at.asc())
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            pending = session.scalars(stmt).all()
            for row in pending:
                self._queue.publish(row.queue, row.envelope)
                row.dispatched = True
                row.dispatched_at = datetime.now(timezone.utc)
                row.dispatch_attempts += 1
                dispatched_count += 1
            session.commit()
        return dispatched_count
