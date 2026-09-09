"""Conflict register (M10). FR-CFL-01-05. Consumes verdicts from
Shruthi's validators, doesn't produce them — `open_conflict`'s `rule`/
`evidence` come from whatever validator (or downstream defect report,
FR-PUB-07) triggered the conflict; this module just records it.

Publication is BLOCKED for any record with an open conflict —
`is_publish_blocked` is a thin boolean wrapper over
`backend.domain.publication_gate.check_open_conflict`, the identical
block-publish primitive P2-08's volume-completeness gate uses ("one
implementation, two callers").

P3-10: workflow (assign, transition through open/under_enquiry/
resolved/referred) and ageing. P3-12: a resolved conflict rejoins the
publish path, and both the resolution and the author land in the audit
trail — `transition` to `resolved` requires a `resolution` string and
records it (not just the actor) precisely so a supervisor can see *why*
it's resolved without opening the pipeline (P3-10's own framing: "the
entry alone must let a supervisor understand the dispute").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from landaudit import append as audit_append
from sqlalchemy.orm import Session

from backend.domain.publication_gate import check_open_conflict
from backend.domain.review_policy import BLOCKING_CONFLICT_STATES
from backend.models.entities import Conflict

VALID_STATES = {"open", "under_enquiry", "resolved", "referred"}


class InvalidConflictTransition(ValueError):
    pass


def open_conflict(session: Session, *, records: list[str], rule: str, evidence: dict, origin: str) -> Conflict:
    conflict = Conflict(records=records, rule=rule, evidence=evidence, origin=origin)
    session.add(conflict)
    session.flush()
    audit_append(session, actor=f"system:{origin}", action="conflict.opened", subject=conflict.id, purpose=rule)
    return conflict


def is_publish_blocked(session: Session, record_id: str) -> bool:
    return check_open_conflict(session, record_id) is not None


def assign(session: Session, conflict_id: str, *, assignee: str, actor: str) -> Conflict:
    conflict = session.get(Conflict, conflict_id)
    if conflict is None:
        raise KeyError(f"no Conflict with id={conflict_id!r}")
    conflict.assignee = assignee
    session.flush()
    audit_append(session, actor=actor, action="conflict.assigned", subject=conflict.id, purpose=assignee)
    return conflict


def transition(
    session: Session, conflict_id: str, *, new_state: str, actor: str, resolution: str | None = None,
) -> Conflict:
    """FR-CFL-04/05. `resolution` is required (and recorded on the audit
    trail, `purpose=resolution`) when moving to `resolved` — P3-12's
    "resolution and author in the audit trail." `resolved -> open` (a
    reopen) is a legitimate transition (a downstream defect, FR-PUB-07,
    can re-raise a conflict thought settled) — only a no-op
    (state -> itself) and a transition to an unrecognised state are
    rejected.
    """
    if new_state not in VALID_STATES:
        raise InvalidConflictTransition(f"unrecognised Conflict state {new_state!r}")
    conflict = session.get(Conflict, conflict_id)
    if conflict is None:
        raise KeyError(f"no Conflict with id={conflict_id!r}")
    if conflict.state == new_state:
        raise InvalidConflictTransition(f"Conflict {conflict_id} is already {new_state!r}")
    if new_state == "resolved" and not resolution:
        raise InvalidConflictTransition("a resolution reason is required to move a Conflict to 'resolved' (P3-12)")

    conflict.state = new_state
    session.flush()
    audit_append(
        session, actor=actor, action=f"conflict.transitioned.{new_state}",
        subject=conflict.id, purpose=resolution,
    )
    return conflict


def list_conflicts(session: Session, *, state: str | None = None) -> list[Conflict]:
    query = session.query(Conflict)
    if state is not None:
        query = query.filter(Conflict.state == state)
    return query.order_by(Conflict.opened_at.asc()).all()


def list_aged(session: Session, *, older_than: timedelta) -> list[Conflict]:
    """P3-10 ageing — open (blocking-state) conflicts that have sat longer
    than `older_than` without resolving."""
    cutoff = datetime.now(timezone.utc) - older_than
    return (
        session.query(Conflict)
        .filter(Conflict.state.in_(BLOCKING_CONFLICT_STATES))
        .filter(Conflict.opened_at < cutoff)
        .order_by(Conflict.opened_at.asc())
        .all()
    )
