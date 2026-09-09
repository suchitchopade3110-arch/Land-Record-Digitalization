"""FR-ING-08 / FR-CFL-03 — the one reusable block-publish primitive. Two
callers need the identical mechanism: this phase's volume-completeness
gate (no record from a volume with an open gap publishes until an
operator resolves or acknowledges it) and Phase 3's conflict register
(no record with an open conflict publishes) — same `GateResult` shape,
different `CheckFn`s, one `evaluate()`.

Real publish orchestration (checking every sibling field, `Record.version`,
FR-PUB-01/02) is Phase 4 scope — `backend.domain.decision._auto_accept`
already notes this. What this module gives Phase 4 (and this phase's own
tests) is the answer to "is this subject blocked right now, and why,"
which is the part that has to exist before any publish path can consult it.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class BlockReason:
    code: str  # machine-readable — never a bare sentence a caller has to parse
    detail: dict


@dataclass(frozen=True)
class GateResult:
    blocked: bool
    reasons: tuple[BlockReason, ...]


class PublishBlocked(Exception):
    """Raised by `attempt_publish` — carries every reason a subject is
    blocked, not just the first one found, so a single failed publish
    attempt names everything an operator needs to resolve."""

    def __init__(self, reasons: tuple[BlockReason, ...]):
        self.reasons = reasons
        super().__init__("; ".join(f"{r.code}: {r.detail}" for r in reasons))


CheckFn = Callable[[Session, str], BlockReason | None]


def evaluate(session: Session, subject_id: str, checks: list[CheckFn]) -> GateResult:
    """Run every check against `subject_id`; blocked if any reports a
    reason. Never short-circuits on the first hit — `attempt_publish`'s
    callers need the complete list."""
    reasons = tuple(r for r in (check(session, subject_id) for check in checks) if r is not None)
    return GateResult(blocked=bool(reasons), reasons=reasons)


def attempt_publish(session: Session, subject_id: str, checks: list[CheckFn]) -> GateResult:
    """Raise `PublishBlocked` if any check reports a reason; otherwise
    return the (unblocked) `GateResult`. `checks` is supplied by the
    caller (batch-completeness checks for a batch subject, conflict checks
    for a record subject) — this function has no opinion on what "publish"
    means for either subject type, only on the block/proceed decision.
    """
    result = evaluate(session, subject_id, checks)
    if result.blocked:
        raise PublishBlocked(result.reasons)
    return result


def check_volume_completeness(session: Session, batch_id: str) -> BlockReason | None:
    """FR-ING-08 — the volume-completeness caller. Imported lazily inside
    the function body to avoid a hard import cycle (`completeness` doesn't
    need to know about the gate; the gate needs one function from it)."""
    from backend.domain.completeness import current_alert

    alert = current_alert(session, batch_id)
    if alert is None:
        return None
    return BlockReason(
        code="volume_gap",
        detail={"batch_id": batch_id, "missing_index_positions": alert.missing_index_positions},
    )


def check_open_conflict(session: Session, record_id: str) -> BlockReason | None:
    """FR-CFL-03 — the conflict-register caller (Phase 3's, wired here
    since it's the same mechanism this phase builds; `backend.domain.
    conflict_register.is_publish_blocked` is a thin wrapper kept for
    callers that only care about the boolean)."""
    from backend.models.entities import Conflict

    open_conflicts = (
        session.query(Conflict)
        .filter(Conflict.records.any(record_id))
        .filter(Conflict.state != "resolved")
        .all()
    )
    if not open_conflicts:
        return None
    return BlockReason(
        code="open_conflict",
        detail={"record_id": record_id, "conflict_ids": [c.id for c in open_conflicts]},
    )
