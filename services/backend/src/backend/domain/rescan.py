"""M2, FR-TRI-01 — `RescanTask` lifecycle. Created from Tharun's
legibility-threshold-breach event; `open_from_threshold_breach`'s
`reason_code` is whatever that event carries (this module doesn't
enumerate or validate reason codes — the legibility scorer that produces
them is Tharun's, not backend's, per Team-Split; the queue and lifecycle
around them is). `owner` is left unset at creation (PRD §11 Q4 — whether
the record room or this system owns rescanning is still open); `assign`
is how it gets set once that's answered or decided per-task.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from landaudit import append as audit_append
from sqlalchemy.orm import Session

from backend.models.entities import RescanTask

VALID_STATES = {"open", "assigned", "closed"}


class InvalidRescanTaskTransition(ValueError):
    pass


def open_from_threshold_breach(session: Session, *, page_id: str, reason_code: str) -> RescanTask:
    task = RescanTask(page_id=page_id, reason_code=reason_code, state="open")
    session.add(task)
    session.flush()
    audit_append(
        session, actor="system:legibility-scorer", action="rescan_task.opened",
        subject=task.id, purpose=reason_code,
    )
    return task


def assign(session: Session, rescan_task_id: str, *, owner: str) -> RescanTask:
    task = session.get(RescanTask, rescan_task_id)
    if task is None:
        raise KeyError(f"no RescanTask with id={rescan_task_id!r}")
    if task.state != "open":
        raise InvalidRescanTaskTransition(f"cannot assign a RescanTask in state {task.state!r}")
    task.owner = owner
    task.state = "assigned"
    session.flush()
    return task


def close(session: Session, rescan_task_id: str, *, actor: str) -> RescanTask:
    task = session.get(RescanTask, rescan_task_id)
    if task is None:
        raise KeyError(f"no RescanTask with id={rescan_task_id!r}")
    if task.state == "closed":
        raise InvalidRescanTaskTransition("RescanTask is already closed")
    task.state = "closed"
    task.closed_at = datetime.now(timezone.utc)
    session.flush()
    audit_append(session, actor=actor, action="rescan_task.closed", subject=task.id)
    return task


def list_aged(session: Session, *, older_than: timedelta) -> list[RescanTask]:
    """Ageing (P2-12): open/assigned tasks that have sat longer than
    `older_than` without closing — the report a supervisor's queue view
    reads from; this module doesn't decide what happens to an aged task,
    only which ones qualify."""
    cutoff = datetime.now(timezone.utc) - older_than
    return (
        session.query(RescanTask)
        .filter(RescanTask.state != "closed")
        .filter(RescanTask.opened_at < cutoff)
        .order_by(RescanTask.opened_at.asc())
        .all()
    )
