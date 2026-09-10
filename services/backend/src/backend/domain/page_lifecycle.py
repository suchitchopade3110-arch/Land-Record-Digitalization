"""P5-06-fix/FR-ANL-01/07 — settles where `page.processed` fires.

**Decision:** a page counts as processed once it has reached a *terminal
decision outcome* for every field extracted from it — not when triage
routes it. The original P5-06 session fired `page.processed` from
`backend.domain.triage.route_page` (envelope pinned + routing decision),
flagged there as an unconfirmed assumption; that placement made the
dashboard's "processed" figure run ahead of the real backlog, since triage
only enqueues a page for extraction — nothing about its content is
decided yet at that point.

"Terminal decision outcome" means every `Extraction` row for the page has
a non-null `routing_outcome` in `backend.domain.decision.VALID_ROUTING_OUTCOMES`
— i.e. `backend.domain.decision.route()` has run for the page's last
remaining field. `route()` calls `mark_processed_if_terminal` (this
module) after every one of its five outcome branches, so the "is every
sibling now terminal" check runs on every field decision, not just
conveniently the last one — that's what actually detects "this was the
last one."

**Call site this moved from:** `backend.domain.triage.route_page` no
longer emits `page.processed` at all — see that function's docstring,
which points back here.

**Replay guard:** `route()` can be called more than once for the same
`Extraction` (a redelivered DECISION_QUEUE message) without
`page.processed` firing twice for the page, because this function checks
for an existing `page.processed` entry (scoped to the page's own shard)
before appending a new one — the append-only audit log has no separate
"already processed" flag table to keep in sync with.
"""
from __future__ import annotations

from landaudit import shard_for, shard_key_for
from landaudit.models import AuditEntry
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.audit_log import record_page_processed
from backend.domain.decision import VALID_ROUTING_OUTCOMES
from backend.models.entities import Batch, Extraction, Page, SourceDocument


def district_for_page(session: Session, page_id: str) -> str | None:
    """Page -> SourceDocument -> Batch -> district. No natural district on
    a `Page`/`Extraction` row itself — same gap `backend.workers
    .ingestion_consumer.handle` resolves for `page.ingested`, done here
    for `page.processed` since the decision engine only ever sees an
    `Extraction` (and, via it, a `page_id`), never a `Batch`."""
    page = session.get(Page, page_id)
    if page is None:
        return None
    doc = session.get(SourceDocument, page.document_id)
    if doc is None:
        return None
    batch = session.get(Batch, doc.batch_id)
    return batch.district if batch else None


def _already_processed(session: Session, page_id: str, *, district: str | None) -> bool:
    shard_key = shard_key_for(district=district) if district else None
    shard_id = shard_for(shard_key) if shard_key else shard_for(page_id)
    existing = session.execute(
        select(AuditEntry.id)
        .where(
            AuditEntry.shard_id == shard_id,
            AuditEntry.action == "page.processed",
            AuditEntry.subject == page_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    return existing is not None


def mark_processed_if_terminal(session: Session, page_id: str) -> bool:
    """Called from `backend.domain.decision.route()` after every field
    decision. Returns True iff this call actually appended the
    `page.processed` event (False on every call before the page's last
    field decides, and on every call after the first one that does — the
    "fires exactly once per page" guarantee)."""
    district = district_for_page(session, page_id)
    if _already_processed(session, page_id, district=district):
        return False

    outcomes = session.execute(
        select(Extraction.routing_outcome).where(Extraction.page_id == page_id)
    ).scalars().all()
    if not outcomes:
        return False  # no extraction rows yet — nothing to be terminal about
    if any(o not in VALID_ROUTING_OUTCOMES for o in outcomes):
        return False  # at least one field still mid-pipeline

    record_page_processed(session, page_id=page_id, district=district)
    return True
