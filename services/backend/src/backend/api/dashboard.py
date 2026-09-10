"""GET /dashboard/metrics — M14/P5-06, FR-ANL-01/07. Every figure this
route returns is a query over `audit_entry` — no second source of truth
(CLAUDE.md's dashboard rule, T5.d). See `backend.domain.dashboard`'s
module docstring for the mapping decisions behind "ingested/processed/
published" and for what P5-06 deliberately does not build (P5-07's other
panels, P5-08's operational-state panel).

FR-ANL-08's inference-cost component is the one PRD-named exception to
"every figure comes from audit_entry" (CLAUDE.md) — not built here (it's
P5-07 scope, and Tharun's observability data doesn't exist yet); when it
lands, it must be its own separately-named path declaring
`"source": "observability"`, never folded into this route.

No permission gate: every figure here is an aggregate count, never a
per-record or personal-data value (`gateway/route_registry.yaml` already
declares `masking: none` for this route) — there is nothing here for
`Permission.RECORD_READ_MASKED` or similar to gate. If dashboard access
itself needs restricting to certain roles later, that is a deliberate
access-control decision to raise, not a mechanical extension of this
route.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.deps import get_session
from backend.domain.dashboard import get_page_activity

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/metrics")
def get_dashboard_metrics(
    district: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    session: Session = Depends(get_session),
) -> dict:
    return {"page_activity": get_page_activity(session, district=district, since=since, until=until)}
