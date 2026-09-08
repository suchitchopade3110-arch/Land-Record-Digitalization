"""GET /closed-sets/{type} — TODO: FR-VAL-09, FR-OCR-07. Joint owner w/ Shruthi.
contracts/openapi/closed-sets.suchit-shruthi.yaml

Hard rule: NEVER expose LRMS-derived sets, only LGD/schema sets (blocked by
PRD §11 Q3/Q9 until resolved). See API-Contracts §4.3.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["closed-sets"])


@router.get("/closed-sets/{type}")
def get_closed_set(type: str, district: str | None = None):
    # TODO: FR-VAL-09 — schema/LGD-derived sets only. Never LRMS-derived.
    raise HTTPException(status_code=501, detail="TODO: FR-VAL-09 not implemented")
