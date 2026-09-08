"""GET /conflicts — TODO: FR-CFL-01-06. Conflict register (M10)."""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts")
def list_conflicts():
    # TODO: FR-CFL-01 — list open/under_enquiry/resolved/referred conflicts.
    raise HTTPException(status_code=501, detail="TODO: FR-CFL-01..06 not implemented")
