"""P3-13/FR-REV-16 — the session anchor `ReviewTask.hour_into_session`
needs. No `contracts/` entity defines where a review session starts or
ends (the build prompt flagged this as a contract gap); resolved here as
a backend-internal table rather than a `contracts/proposals/` entry,
because nothing outside `services/backend` reads or writes it — the same
"internal, no cross-service consumer, no contract needed" reasoning
`INGESTION_QUEUE` used in Phase 2. See PHASE3.md.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.domain.review_policy import REVIEW_SESSION_GAP
from backend.models.entities import ReviewSession


def touch_session(session: Session, *, actor: str, at: datetime | None = None) -> tuple[ReviewSession, int]:
    """Get-or-create the officer's current `ReviewSession` and return it
    alongside `hour_into_session` for `at` (defaults to now). Closes a
    stale session (idle longer than `REVIEW_SESSION_GAP`) and opens a
    fresh one rather than letting `hour_into_session` grow across a
    day-long gap between shifts.
    """
    now = at or datetime.now(timezone.utc)

    open_session = (
        session.query(ReviewSession)
        .filter(ReviewSession.actor == actor, ReviewSession.ended_at.is_(None))
        .order_by(ReviewSession.started_at.desc())
        .first()
    )

    if open_session is not None and (now - open_session.last_activity_at) > REVIEW_SESSION_GAP:
        open_session.ended_at = open_session.last_activity_at
        session.flush()
        open_session = None

    if open_session is None:
        open_session = ReviewSession(actor=actor, started_at=now, last_activity_at=now)
        session.add(open_session)
        session.flush()
    else:
        open_session.last_activity_at = now
        session.flush()

    hour_into_session = int((now - open_session.started_at).total_seconds() // 3600)
    return open_session, hour_into_session
