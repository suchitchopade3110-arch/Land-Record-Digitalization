"""GET /review-tasks, POST /review-tasks/next, GET /review-tasks/{id}/crop,
POST /review-tasks/{id}/submit, POST /review-tasks/{id}/skip,
POST /review-tasks/pending-corrections/{id}/confirm — FR-REV-01-16 (P3, P6/T1-01).

Reasons rendered come from Shruthi (rule failures) and Tharun (calibrator
features) — this layer renders both identically (FR-REV-11), and never
leaks `source_stream` to the client (`strip_review_task_internals`,
invariant 4 — CLAUDE.md). `actor` is resolved from identity (`identity.actor`)
via `require_permission` RBAC dependencies.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.auth import Permission, require_permission
from backend.api.deps import get_session
from backend.api.serializers import ReviewTaskPublicView, strip_review_task_internals
from backend.domain.access_control import Identity
from backend.domain.correction import (
    PendingCorrectionAlreadyResolved,
    SameActorCannotConfirm,
    confirm_pending_correction,
)
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

_require_review_claim = require_permission(Permission.REVIEW_CLAIM)
_require_review_submit = require_permission(Permission.REVIEW_SUBMIT)
_require_review_confirm = require_permission(Permission.REVIEW_CONFIRM)


@router.get("/review-tasks", response_model=list[ReviewTaskPublicView])
def list_review_tasks(
    identity: Identity = Depends(_require_review_claim),
    session: Session = Depends(get_session),
) -> list[ReviewTaskPublicView]:
    tasks = session.query(ReviewTask).filter(ReviewTask.closed_at.is_(None)).order_by(ReviewTask.opened_at.asc()).all()
    return [strip_review_task_internals(t) for t in tasks]


@router.post("/review-tasks/next", response_model=ReviewTaskPublicView | None)
def fetch_next(
    identity: Identity = Depends(_require_review_claim),
    session: Session = Depends(get_session),
) -> ReviewTaskPublicView | None:
    """P3-03 — keyboard-first, one request per action. Returns `null`
    (204-shaped, but a body so a thin client doesn't special-case status
    codes) when the queue is empty, never an error."""
    task = claim_next(session, actor=identity.actor)
    session.commit()
    if task is None:
        return None
    return strip_review_task_internals(task)


@router.get("/review-tasks/{task_id}/crop")
def fetch_crop(
    task_id: str,
    identity: Identity = Depends(_require_review_claim),
    session: Session = Depends(get_session),
) -> dict:
    """P3-04 — signed, short-TTL, access-controlled. Claimant check ensures
    only the assigned officer can fetch the crop for an active task."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such review task")
    if task.assignee != identity.actor:
        raise HTTPException(
            status_code=403,
            detail=f"task {task_id} is assigned to {task.assignee!r}, not {identity.actor!r}",
        )
    try:
        url = crop_url_for(session, task, actor=identity.actor)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    session.commit()
    return {"crop_url": url}


@router.post("/review-tasks/{task_id}/submit")
def submit_review_task(
    task_id: str,
    corrected_value: str = Body(...),
    verdict: str | None = Body(None),
    identity: Identity = Depends(_require_review_submit),
    session: Session = Depends(get_session),
) -> dict:
    """P3-05/07/08/09. `verdict` (`agree`/`disagree`) is only meaningful
    when this task happens to be an `AuditSample` task — the officer never
    knows that (FR-REV-11), so the field is always offered and ignored
    when there's nothing to record it against."""
    try:
        result = submit(
            session, task_id=task_id, actor=identity.actor, corrected_value=corrected_value, verdict=verdict,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ReviewTaskNotClaimable, NotTheClaimant) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return result


@router.post("/review-tasks/{task_id}/skip")
def skip_review_task(
    task_id: str,
    identity: Identity = Depends(_require_review_submit),
    session: Session = Depends(get_session),
) -> dict:
    try:
        skip(session, task_id=task_id, actor=identity.actor)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (ReviewTaskNotClaimable, NotTheClaimant) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    return {"task_id": task_id, "status": "skipped"}


@router.post("/review-tasks/pending-corrections/{pending_id}/confirm", response_model=dict)
def confirm_pending_correction_route(
    pending_id: str,
    identity: Identity = Depends(_require_review_confirm),
    session: Session = Depends(get_session),
) -> dict:
    """T1-01 / FR-REV-12 — maker-checker confirmation. Gated by REVIEW_CONFIRM;
    confirmer must be distinct from both the maker and the review claimant."""
    try:
        correction = confirm_pending_correction(session, pending_id=pending_id, actor=identity.actor)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PendingCorrectionAlreadyResolved as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except SameActorCannotConfirm as e:
        session.commit()
        raise HTTPException(status_code=403, detail=str(e)) from e
    session.commit()
    return {
        "pending_id": pending_id,
        "correction_id": correction.id,
        "status": "confirmed",
        "confirmed_by": identity.actor,
    }
