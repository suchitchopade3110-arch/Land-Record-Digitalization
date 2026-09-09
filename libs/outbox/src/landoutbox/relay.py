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

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from landoutbox.models import OutboxMessage
from landqueue.port import QueuePort


class Relay:
    def __init__(self, session_factory: sessionmaker[Session], queue: QueuePort):
        self._session_factory = session_factory
        self._queue = queue

    def drain_once(self, *, batch_size: int = 100) -> int:
        """Publish up to `batch_size` undispatched messages, oldest first.
        Returns the count actually dispatched. Each message is published
        and marked dispatched in its own short transaction, so one
        message's failure doesn't roll back the others in the batch."""
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
            message_ids = [m.id for m in pending]
            queues_and_envelopes = [(m.queue, m.envelope) for m in pending]
            session.commit()  # release the row lock before the (slower) network publish call

        for message_id, (queue, envelope) in zip(message_ids, queues_and_envelopes):
            self._queue.publish(queue, envelope)
            with self._session_factory() as session:
                row = session.get(OutboxMessage, message_id)
                if row is None or row.dispatched:
                    continue  # already handled by a concurrent relay instance
                row.dispatched = True
                row.dispatched_at = datetime.now(timezone.utc)
                row.dispatch_attempts += 1
                session.commit()
            dispatched_count += 1
        return dispatched_count
