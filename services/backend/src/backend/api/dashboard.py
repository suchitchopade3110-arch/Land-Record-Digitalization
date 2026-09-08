"""GET /dashboard/metrics — TODO: FR-ANL-01/07. Figures derived only from the
audit log — no second source of truth."""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/metrics")
def get_dashboard_metrics():
    # TODO: FR-ANL-01 — compute every figure from the append-only audit log.
    raise HTTPException(status_code=501, detail="TODO: FR-ANL-01/07 not implemented")
