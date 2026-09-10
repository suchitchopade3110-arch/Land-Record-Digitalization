"""GET /closed-sets/{type} — FR-VAL-09, FR-OCR-07. Joint owner w/ Shruthi.
contracts/openapi/closed-sets.suchit-shruthi.yaml

Hard rule: NEVER expose LRMS-derived sets, only LGD/schema sets (blocked by
PRD §11 Q3/Q9 until resolved). See API-Contracts §4.3.

P5-05: the corpus-partition rule is enforced in
`backend.domain.closed_sets`, keyed off each entry's `provenance` column,
never its self-reported `source` — see that module's docstring and T5.c.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_session
from backend.config import config_client
from backend.domain.closed_sets import ClosedSetTypeNotFound, get_closed_set

router = APIRouter(tags=["closed-sets"])


@router.get("/closed-sets/{type}")
def get_closed_set_route(
    type: str,
    district: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    try:
        return get_closed_set(session, config_client, type, district)
    except ClosedSetTypeNotFound:
        raise HTTPException(status_code=404, detail=f"no closed set configured for type={type!r}")
