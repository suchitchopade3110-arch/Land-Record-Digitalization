"""§07 Availability — "ingestion may queue during an outage, it must not
reject." This mostly falls out of ADR-005's transactional outbox rather
than needing new machinery: `backend.domain.ingest.ingest_document` never
calls the broker directly — it writes an `outbox_message` row in the same
Postgres transaction as the `SourceDocument` row, and `landoutbox.Relay`
drains to the real broker whenever one is reachable. A request that
reaches `/documents` never blocks on — or fails because of — Redis being
down; it only needs Postgres, which is a much smaller availability
surface to protect than "and the broker too."

What this module adds on top of that is purely observational: a
queue-depth *ceiling* is a dashboard signal (M14, Phase 5 scope, not yet
built — `check_backlog` is what that aggregation will read once it
exists), never a reason to reject an upload. `check_backlog` counts
undispatched `outbox_message` rows rather than asking the broker directly,
since the backlog that actually matters for "is ingestion falling behind"
is the one Postgres is durably holding, broker-reachable or not.
"""
from __future__ import annotations

from dataclasses import dataclass

from landoutbox.models import OutboxMessage
from sqlalchemy.orm import Session

DEFAULT_BACKLOG_CEILING = 10_000


@dataclass(frozen=True)
class BacklogStatus:
    undispatched_count: int
    ceiling: int
    over_ceiling: bool  # a dashboard signal only — never gates accepting an upload


def check_backlog(session: Session, *, queue: str | None = None, ceiling: int = DEFAULT_BACKLOG_CEILING) -> BacklogStatus:
    query = session.query(OutboxMessage).filter(OutboxMessage.dispatched.is_(False))
    if queue is not None:
        query = query.filter(OutboxMessage.queue == queue)
    count = query.count()
    return BacklogStatus(undispatched_count=count, ceiling=ceiling, over_ceiling=count > ceiling)
