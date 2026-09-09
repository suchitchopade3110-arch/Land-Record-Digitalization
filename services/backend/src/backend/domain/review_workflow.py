"""P3-03/04/05/06 — the review task API's domain logic: fetch-next,
fetch-crop, submit, skip. FR-REV-01/02.

Indistinguishability (FR-REV-11, P3-06) is enforced by construction here,
not by a branch this module has to remember: every function below reads
`ReviewTask` with the identical query regardless of `source_stream` — the
column is never part of a `WHERE`, an `ORDER BY`, or a conditional. The
claim query in `claim_next` is the one place ordering matters at all
(`opened_at ASC`), and it's the same statement for a routed and an audit
task; a byte-diff/timing test (`tests/contract/test_review_workflow_and_maker_checker.py`)
checks this holds, not just that it looks like it should.
"""
from __future__ import annotations

from datetime import datetime, timezone

from landaudit import append as audit_append
from landstorage import get_store
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.audit_sample_verdict import record_officer_verdict
from backend.domain.correction import submit_correction
from backend.domain.review_policy import CROP_URL_TTL_SECONDS
from backend.domain.review_session import touch_session
from backend.models.entities import AuditSample, Extraction, Page, ReviewTask


class ReviewTaskNotClaimable(ValueError):
    pass


class NotTheClaimant(ValueError):
    pass


def claim_next(session: Session, *, actor: str) -> ReviewTask | None:
    """FR-REV-01/02 — one request, one task. `SELECT ... FOR UPDATE SKIP
    LOCKED` so two officers hitting fetch-next concurrently never claim
    the same row and never block on each other; ordered by `opened_at`
    only — no predicate or ordering term reads `source_stream` (FR-REV-11).
    Returns `None` when the queue is empty, never a 4xx — an empty queue
    is a normal state, not an error.
    """
    task = session.execute(
        select(ReviewTask)
        .where(ReviewTask.assignee.is_(None), ReviewTask.closed_at.is_(None))
        .order_by(ReviewTask.opened_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if task is None:
        return None

    _review_session, hour_into_session = touch_session(session, actor=actor)
    task.assignee = actor
    task.hour_into_session = hour_into_session
    session.flush()
    return task


def crop_url_for(session: Session, task: ReviewTask, *, actor: str) -> str:
    """P3-04 — access-controlled, short-TTL signed URL. The audit entry
    records crop *identity* (`page_id`/`bbox`) and nothing dereferenceable
    — never the signed URL, never the value itself (FR-SEC-08)."""
    extraction = session.get(Extraction, task.extraction_id)
    if extraction is None:
        raise KeyError(f"ReviewTask {task.id} has no matching Extraction {task.extraction_id}")
    page = session.get(Page, extraction.page_id)
    if page is None or not page.storage_uri:
        raise ValueError(f"Page {extraction.page_id} has no stored image to crop from")

    signed = get_store().sign_get(page.storage_uri, CROP_URL_TTL_SECONDS)
    url = f"{signed}&bbox={extraction.bbox}" if "?" in signed else f"{signed}?bbox={extraction.bbox}"

    audit_append(
        session, actor=actor, action="review.crop.delivered",
        subject=extraction.id, purpose="review_crop",
        # crop identity only — page_id/bbox, never the signed URL or the value
        value_hash=None,
    )
    session.flush()
    return url


def submit(
    session: Session, *, task_id: str, actor: str, corrected_value: str, verdict: str | None = None,
) -> dict:
    """FR-REV-03/15 passthrough of `reason` is a client-side rendering
    concern (already handled — `serializers.strip_review_task_internals`
    carries `reason` verbatim); this function's job is the write path:
    the correction (P3-08, possibly held for maker-checker, P3-07), the
    audit-sample verdict if this task is one (P3-09, never conditioned on
    telling the officer it is), and closing the task.
    """
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise KeyError(f"no ReviewTask with id={task_id!r}")
    if task.closed_at is not None:
        raise ReviewTaskNotClaimable(f"ReviewTask {task_id} is already closed")
    if task.assignee != actor:
        raise NotTheClaimant(f"ReviewTask {task_id} is claimed by {task.assignee!r}, not {actor!r}")

    extraction = session.get(Extraction, task.extraction_id)
    result = submit_correction(
        session, extraction=extraction, corrected=corrected_value, actor=actor, stream=task.source_stream,
    )

    audit_sample = session.execute(
        select(AuditSample).where(AuditSample.review_task_id == task.id)
    ).scalar_one_or_none()
    if audit_sample is not None and verdict is not None:
        record_officer_verdict(session, audit_sample, verdict=verdict, actor=actor)

    task.closed_at = datetime.now(timezone.utc)
    session.flush()
    audit_append(session, actor=actor, action="review.task.submit", subject=task.id, purpose="review_submit")

    is_pending = hasattr(result, "state") and getattr(result, "state", None) == "pending"
    return {
        "task_id": task.id,
        "extraction_id": extraction.id,
        "correction_id": None if is_pending else result.id,
        "pending_correction_id": result.id if is_pending else None,
        "maker_checker_pending": is_pending,
    }


def skip(session: Session, *, task_id: str, actor: str) -> None:
    """FR-REV-01 — release the task back to the pool rather than closing
    it, so `claim_next` can hand it to another officer. Only the current
    claimant may skip their own claim."""
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise KeyError(f"no ReviewTask with id={task_id!r}")
    if task.closed_at is not None:
        raise ReviewTaskNotClaimable(f"ReviewTask {task_id} is already closed")
    if task.assignee != actor:
        raise NotTheClaimant(f"ReviewTask {task_id} is claimed by {task.assignee!r}, not {actor!r}")

    task.assignee = None
    session.flush()
    audit_append(session, actor=actor, action="review.task.skip", subject=task.id)
