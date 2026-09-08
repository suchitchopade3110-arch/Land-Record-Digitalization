"""GET /review-tasks — TODO: FR-REV-01-16.
Reasons rendered come from Shruthi (rule failures) and Tharun (calibrator
features) — this layer must render both identically (FR-REV-11), and must
never leak `source_stream` to the client.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["review-tasks"])


@router.get("/review-tasks")
def list_review_tasks():
    # TODO: FR-REV-11 — serialize ReviewTask without ever including source_stream.
    raise HTTPException(status_code=501, detail="TODO: FR-REV-01..16 not implemented")
