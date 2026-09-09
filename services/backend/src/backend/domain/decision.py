"""M8→M9/M10/M12 decision engine. FR-CNF-04, FR-CFL-01. Routes each
`Extraction` to exactly one of the **five** outcomes the PRD's §04 diagram
draws — auto-accept, audit-sample, targeted review, conflict, or outside
the calibrated regime — kept structurally separate (three different
reasons — uncertainty, contradiction, novelty — with three different
destinations; collapsing any two of them is the design bug the brief calls
out explicitly). This worker does not compute `routing_outcome` itself —
that's Tharun's confidence/novelty engine (M8) — it only acts on the value
already set on the `Extraction` it receives.
"""
from __future__ import annotations

from landaudit import append as audit_append
from sqlalchemy.orm import Session

from backend.models.entities import AuditSample, Conflict, Extraction, ReviewTask

VALID_ROUTING_OUTCOMES = {
    "auto_accept",
    "audit_sample",
    "review",
    "conflict",
    "outside_calibrated_regime",
}


class UnroutableExtraction(ValueError):
    pass


def route(session: Session, extraction_id: str) -> dict:
    """Dispatch on `Extraction.routing_outcome` (already set upstream by
    Tharun's M8). Returns a small dict describing what was created, for
    the caller (the queue worker) to log/publish onward. Every branch
    writes an audit event — FR-PUB-03 covers every automated decision,
    not just human ones.
    """
    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise KeyError(f"no Extraction with id={extraction_id!r}")
    outcome = extraction.routing_outcome
    if outcome not in VALID_ROUTING_OUTCOMES:
        raise UnroutableExtraction(f"Extraction {extraction_id} has routing_outcome={outcome!r}")

    if outcome == "auto_accept":
        result = _auto_accept(session, extraction)
    elif outcome == "audit_sample":
        result = _audit_sample(session, extraction)
    elif outcome == "review":
        result = _targeted_review(session, extraction)
    elif outcome == "conflict":
        result = _open_conflict_placeholder(session, extraction)
    else:  # outside_calibrated_regime
        result = _outside_calibrated_regime(session, extraction)

    audit_append(
        session, actor="system:decision-engine", action=f"decision.route.{outcome}", subject=extraction.id,
    )
    session.flush()
    return result


def _auto_accept(session: Session, extraction: Extraction) -> dict:
    # FR-CNF-04: "a record publishes when its last field clears and no
    # conflict is attached." Whole-record publish orchestration
    # (checking every sibling field, `Record.version`, FR-PUB-01/02) is
    # Phase 4 scope (publication/provenance) — this P0 path marks the
    # field decided and stops there, which is honest about what's built:
    # not "published," just "cleared for publication."
    return {"outcome": "auto_accept", "extraction_id": extraction.id}


def _audit_sample(session: Session, extraction: Extraction) -> dict:
    """FR-CNF-07 — a configurable fraction of auto-accepted fields is
    injected into the review queue as an ordinary review task,
    indistinguishable from a routed one (FR-REV-11 — enforced at
    serialization, `backend.api.serializers.strip_review_task_internals`,
    not by this function withholding information from the row itself)."""
    task = ReviewTask(extraction_id=extraction.id, reason=None, source_stream="audit")
    session.add(task)
    session.flush()
    sample = AuditSample(extraction_id=extraction.id, review_task_id=task.id, model_confidence=extraction.calibrated_confidence)
    session.add(sample)
    session.flush()
    return {"outcome": "audit_sample", "review_task_id": task.id, "audit_sample_id": sample.id}


def _targeted_review(session: Session, extraction: Extraction) -> dict:
    task = ReviewTask(extraction_id=extraction.id, reason=None, source_stream="routed")
    session.add(task)
    session.flush()
    return {"outcome": "review", "review_task_id": task.id}


def _open_conflict_placeholder(session: Session, extraction: Extraction) -> dict:
    """FR-CFL-01 — records failing a cross-record validator enter the
    conflict register instead of the publish path. This decision engine
    receives `routing_outcome == "conflict"` already decided upstream (by
    a validator's verdict, per the queue topology's Validation →
    Confidence/Novelty → Decision hop) — it does not itself decide
    *which* records or rule are implicated; that detail belongs on the
    `Conflict.evidence` payload the triggering validator's message
    carries, and this P0 path records a placeholder pending that wiring
    to Shruthi's validator output (M10 is `services/backend`'s scope;
    what evidence the validator attaches is Shruthi's M6)."""
    conflict = Conflict(
        records=[extraction.id],
        rule="pending-validator-evidence-wiring",
        evidence={"extraction_id": extraction.id},
        origin="validator",
    )
    session.add(conflict)
    session.flush()
    return {"outcome": "conflict", "conflict_id": conflict.id}


def _outside_calibrated_regime(session: Session, extraction: Extraction) -> dict:
    """FR-CNF-14 — never auto-accepted at any calibrated confidence, and a
    *cluster* of novel pages raises one operational alert rather than a
    stream of per-field review tasks. This P0 path records the single
    audit event per field (the alert-clustering logic itself is Tharun's
    M8/dashboard aggregation territory, FR-ANL-12) — no `ReviewTask` is
    created, matching the PRD's explicit "not a stream of per-field
    tasks" requirement.
    """
    return {"outcome": "outside_calibrated_regime", "extraction_id": extraction.id}
