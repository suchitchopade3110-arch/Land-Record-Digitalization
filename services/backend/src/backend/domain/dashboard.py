"""M14/P5-06 — FR-ANL-01/07. Every figure here is a query over
`audit_entry` (CLAUDE.md's dashboard rule) — no separately incremented
counter, no in-memory tally, nothing that could drift from the log it
summarizes. If a number cannot be expressed this way, it does not belong
in this module — see `api/dashboard.py`'s docstring for the one figure
(FR-ANL-08's inference-cost component, not built here) that is a named,
bounded exception to this rule, and this module's own docstring below for
what is scoped OUT of P5-06 specifically.

FR-ANL-01 asks for "pages ingested / processed / published, by district
and over time." Building this surfaced two real gaps this module's
supporting audit-instrumentation changes close (see `backend.domain.
audit_log.record_page_ingested`/`record_page_processed`, and
`landaudit.chain.append`'s new `district` parameter):

1. `district` was already threaded through several audit call sites to
   compute a *shard key* (a hash), but the literal district string was
   never preserved anywhere queryable — `AuditEntry.district` (P5-06)
   fixes that, as plain metadata, deliberately not part of the hash
   chain's cryptographic material (see `chain.append`'s docstring for
   why this task doesn't touch that).
2. There was no page-granular "ingested" event (only document-level) and
   no "processed" event at all. Two new action types close this:
   `page.ingested` (one per `Page` row created) and `page.processed`
   (fired once, on a page's first — never a replayed — triage routing).

Mapping decisions, stated plainly rather than guessed at silently (the
PRD's own FR-ANL-01 wording is not available in this session — supplied
out of band per CLAUDE.md):
  - "ingested" = `page.ingested` — exact, page-granular, unambiguous.
  - "processed" = `page.processed` — an assumption ("left the acquisition
    band with a pinned envelope and a routing decision"), not a term this
    codebase had an existing unambiguous definition for. Confirm against
    the PRD before treating this figure as final.
  - "published" = `record.published` — a proxy. Publication happens at
    `Record` granularity (FR-PUB-01), not per page; there is no
    page-level publish event, so this counts published *records*, not
    published *pages*, as the closest existing terminal-publication
    signal.

Out of scope for P5-06 (separate task IDs, reported rather than built):
P5-07's other panels (auto-accept rate with its confidence interval,
officer-minutes decomposition, cost-per-page — several depend on
Tharun's audit-stream data, which does not exist yet) and P5-08's
operational-state panel.
"""
from __future__ import annotations

from datetime import date, datetime

from landaudit.models import AuditEntry
from sqlalchemy import func, select
from sqlalchemy.orm import Session

PAGE_ACTIVITY_ACTIONS: dict[str, str] = {
    "page.ingested": "pages_ingested",
    "page.processed": "pages_processed",
    "record.published": "pages_published",
}


def get_page_activity(
    session: Session,
    *,
    district: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """FR-ANL-01 — one row per `(district, date)` actually observed in
    `audit_entry` among `PAGE_ACTIVITY_ACTIONS`, each carrying its three
    counts. A `(district, date)` combination with zero activity of a
    given kind simply doesn't raise that count above zero — it is never
    a row that doesn't appear, and a `(district, date)` with no activity
    of *any* kind never appears at all (there is nothing to report,
    which is different from reporting zero — CLAUDE.md's "silence and
    absence are different answers," applied here to what counts as a
    reportable row rather than to a lookup's existence).

    A single query, grouped by `(district, date(at), action)` — the SQL
    equivalent of "count these action types, split by day and district" —
    then pivoted into one row per `(district, date)` in Python. This is
    still one query over `audit_entry`, not several disagreeing sources;
    the pivot is presentation, not a second count.
    """
    stmt = (
        select(
            AuditEntry.district,
            func.date(AuditEntry.at).label("day"),
            AuditEntry.action,
            func.count().label("n"),
        )
        .where(AuditEntry.action.in_(PAGE_ACTIVITY_ACTIONS))
        .group_by(AuditEntry.district, func.date(AuditEntry.at), AuditEntry.action)
        .order_by(func.date(AuditEntry.at), AuditEntry.district)
    )
    if district is not None:
        stmt = stmt.where(AuditEntry.district == district)
    if since is not None:
        stmt = stmt.where(AuditEntry.at >= since)
    if until is not None:
        stmt = stmt.where(AuditEntry.at < until)

    rows: dict[tuple[str | None, date], dict] = {}
    for row_district, day, action, count in session.execute(stmt).all():
        day_str = day.isoformat() if isinstance(day, date) else str(day)
        key = (row_district, day_str)
        bucket = rows.setdefault(
            key,
            {
                "district": row_district,
                "date": day_str,
                "pages_ingested": 0,
                "pages_processed": 0,
                "pages_published": 0,
            },
        )
        bucket[PAGE_ACTIVITY_ACTIONS[action]] = count

    return sorted(rows.values(), key=lambda r: (r["date"], r["district"] or ""))
