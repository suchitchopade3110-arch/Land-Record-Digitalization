"""GET /review-tasks, POST /review-tasks/next, GET /review-tasks/{id}/crop,
POST /review-tasks/{id}/submit, POST /review-tasks/{id}/skip — FR-REV-01-16
(P3-03/04/05/06/07/09).

Reasons rendered come from Shruthi (rule failures) and Tharun (calibrator
features) — this layer renders both identically (FR-REV-11), and never
leaks `source_stream` to the client (`strip_review_task_internals`,
invariant 4 — CLAUDE.md). `actor` is accepted as an explicit request field
rather than resolved from a session, pending FR-SEC-01 (auth is still a
`NotImplementedError` stub, `backend.api.auth`) — every function below
that needs to know who's calling takes it as an argument for the same
reason `backend.domain.rescan.assign` does.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.deps import get_session
from backend.api.serializers import ReviewTaskPublicView, strip_review_task_internals
from backend.domain.review_workflow import (
    NotTheClaimant,
    ReviewTaskNotClaimable,
    claim_next,
    crop_url_for,
    skip,
    submit,
)
from backend.models.entities import ReviewTask

router = APIRouter(tags=["review-tasks"])


@router.get("/review-tasks", response_model=list[ReviewTaskPublicView])
def list_review_tasks(session: Session = Depends(get_session)) -> list[ReviewTaskPublicView]:
    tasks = session.query(ReviewTask).filter(ReviewTask.closed_at.is_(None)).order_by(ReviewTask.opened_at.asc()).all()
    return [strip_review_task_internals(t) for t in tasks]


@router.post("/review-tasks/next", response_model=ReviewTaskPublicView | None)
def fetch_next(actor: str = Body(..., embed=True), session: Session = Depends(get_session)) -> ReviewTaskPublicView | None:
    """P3-03 — keyboard-first, one request per action. Returns `null`
    (204-shaped, but a body so a thin client doesn't special-case status
    codes) when the queue is empty, never an error."""
    task = claim_next(session, actor=actor)
    session.commit()
    if task is None:
        return None
    return strip_review_task_internals(task)


@router.get("/review-tasks/{task_id}/crop")
def fetch_crop(task_id: str, actor: str, session: Session = Depends(get_session)) -> dict:
    """P3-04 — signed, short-TTL, access-controlled. `actor` is a query
    param here (a GET, so no body) — same "who's asking" note as the
    module docstring."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such review task")
    try:
        url = crop_url_for(session, task, actor=actor)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    session.commit()
    return {"crop_url": url}


@router.post("/review-tasks/{task_id}/submit")
def submit_review_task(
    task_id: str,
    actor: str = Body(...),
    corrected_value: str = Body(...),
    verdict: str | None = Body(None),
    session: Session = Depends(get_session),
) -> dict:
    """P3-05/07/08/09. `verdict` (`agree`/`disagree`) is only meaningful
    when this task happens to be an `AuditSample` task — the officer never
    knows that (FR-REV-11), so the field is always offered and ignored
    when there's nothing to record it against."""
    try:
        result = submit(session, task_id=task_id, actor=actor, corrected_value=corrected_value, verdict=verdict)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ReviewTaskNotClaimable, NotTheClaimant) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return result


@router.post("/review-tasks/{task_id}/skip")
def skip_review_task(task_id: str, actor: str = Body(..., embed=True), session: Session = Depends(get_session)) -> dict:
    try:
        skip(session, task_id=task_id, actor=actor)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ReviewTaskNotClaimable, NotTheClaimant) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return {"task_id": task_id, "status": "skipped"}
