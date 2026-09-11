"""GET /dashboard/metrics — M14/P5-06, FR-ANL-01/07, FR-SEC-01 (T1-01). Every figure this
route returns is a query over `audit_entry` — no second source of truth
(CLAUDE.md's dashboard rule, T5.d). See `backend.domain.dashboard`'s
module docstring for the mapping decisions behind "ingested/processed/
published" and for what P5-06 deliberately does not build (P5-07's other
panels, P5-08's operational-state panel).

Gated by Permission.DASHBOARD_READ (supervisor, auditor, administrator).
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.api.auth import Permission, require_permission
from backend.api.deps import get_session
from backend.domain.access_control import Identity
from backend.domain.dashboard import PAGE_ACTIVITY_UNITS, get_page_activity

router = APIRouter(tags=["dashboard"])

_require_dashboard_read = require_permission(Permission.DASHBOARD_READ)


@router.get("/dashboard/metrics")
def get_dashboard_metrics(
    district: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    identity: Identity = Depends(_require_dashboard_read),
    session: Session = Depends(get_session),
) -> dict:
    # P5-06-units — `units` names what each `page_activity` row's counts
    # actually count (pages vs records), explicitly, in the response
    # itself — see `backend.domain.dashboard`'s docstring ("published",
    # settled) for why `records_published` isn't just a differently-named
    # page count.
    return {
        "page_activity": get_page_activity(session, district=district, since=since, until=until),
        "units": PAGE_ACTIVITY_UNITS,
    }
