"""GET /review-tasks — FR-REV-01-16 (P0 subset: list + serialize).
Reasons rendered come from Shruthi (rule failures) and Tharun (calibrator
features) — this layer renders both identically (FR-REV-11), and never
leaks `source_stream` to the client (`strip_review_task_internals`,
invariant 4 — CLAUDE.md).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.serializers import ReviewTaskPublicView, strip_review_task_internals
from backend.models.base import session_factory
from backend.models.entities import ReviewTask

router = APIRouter(tags=["review-tasks"])


def get_session():
    factory = session_factory()
    with factory() as session:
        yield session


@router.get("/review-tasks", response_model=list[ReviewTaskPublicView])
def list_review_tasks(session: Session = Depends(get_session)) -> list[ReviewTaskPublicView]:
    tasks = session.query(ReviewTask).filter(ReviewTask.closed_at.is_(None)).order_by(ReviewTask.opened_at.asc()).all()
    return [strip_review_task_internals(t) for t in tasks]
