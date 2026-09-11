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

import uuid
from datetime import datetime, timezone

from landaudit import append as audit_append
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.review_policy import NOVELTY_CLUSTER_ALERT_DEDUP_WINDOW
from backend.models.entities import (
    AuditSample,
    Conflict,
    DecisionRecord,
    Extraction,
    OperationalAlert,
    ReviewTask,
)

VALID_ROUTING_OUTCOMES = {
    "auto_accept",
    "audit_sample",
    "review",
    "conflict",
    "outside_calibrated_regime",
}


class UnroutableExtraction(ValueError):
    """P3-01 — an unrecognised `routing_outcome` is a hard failure, never
    a silent default to `review`. This module has no DLQ to hand it to
    yet (`landqueue.port` names dead-letter handling as Phase 5 scope) —
    raising here is what makes the queue consumer
    (`backend.workers.decision_engine.handle`) fail the message loudly
    instead of committing a guessed outcome, which is the property that
    actually matters until Phase 5's DLQ+alert wiring lands."""


class ExtractionNotFoundError(KeyError):
    """Raised when the Extraction row does not exist in Postgres."""


class PayloadMismatchError(ValueError):
    """Raised when message payload attributes mismatch DB row attributes (D1-A)."""


class AlreadyDecidedWithDifferentOutcome(ValueError):
    """Raised when an extraction is already decided and a message carries a different outcome (D2)."""


def route(
    session: Session,
    extraction_id: str,
    *,
    novelty_cluster_id: str | None = None,
    envelope_id: str | None = None,
) -> dict:
    """Dispatch on `Extraction.routing_outcome` (already set upstream by
    Tharun's M8). Returns a small dict describing what was created, for
    the caller (the queue worker) to log/publish onward. Every branch
    writes an audit event — FR-PUB-03 covers every automated decision,
    not just human ones.

    Idempotent per D2: exactly one terminal decision per extraction_id,
    guaranteed by unique constraint on decision_record. A replay returns
    the stored result without creating new tasks, conflicts, alerts, or audit entries.
    """
    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise ExtractionNotFoundError(f"no Extraction with id={extraction_id!r}")

    outcome = extraction.routing_outcome
    if outcome not in VALID_ROUTING_OUTCOMES:
        raise UnroutableExtraction(f"Extraction {extraction_id} has routing_outcome={outcome!r}")

    # Check if already decided (D2 replay idempotency)
    existing_decision = session.execute(
        select(DecisionRecord).where(DecisionRecord.extraction_id == extraction_id)
    ).scalar_one_or_none()

    if existing_decision is not None and isinstance(existing_decision, DecisionRecord):
        if existing_decision.outcome != outcome:
            raise AlreadyDecidedWithDifferentOutcome(
                f"Extraction {extraction_id} was already decided as {existing_decision.outcome!r}, cannot change to {outcome!r}"
            )
        # Idempotent replay: return stored representation without side effects
        if outcome == "auto_accept":
            return {"outcome": "auto_accept", "extraction_id": extraction.id, "replayed": True}
        if outcome == "review":
            task = session.execute(select(ReviewTask).where(ReviewTask.extraction_id == extraction_id)).scalar_one_or_none()
            return {"outcome": "review", "review_task_id": task.id if task else None, "replayed": True}
        if outcome == "audit_sample":
            task = session.execute(select(ReviewTask).where(ReviewTask.extraction_id == extraction_id)).scalar_one_or_none()
            sample = session.execute(select(AuditSample).where(AuditSample.extraction_id == extraction_id)).scalar_one_or_none()
            return {
                "outcome": "audit_sample",
                "review_task_id": task.id if task else None,
                "audit_sample_id": sample.id if sample else None,
                "replayed": True,
            }
        if outcome == "conflict":
            conflict = session.execute(select(Conflict).where(Conflict.records.contains([extraction_id]))).scalar_one_or_none()
            return {"outcome": "conflict", "conflict_id": conflict.id if conflict else None, "replayed": True}
        # outside_calibrated_regime
        alert = session.execute(select(OperationalAlert).where(OperationalAlert.extraction_ids.contains([extraction_id]))).scalar_one_or_none()
        return {
            "outcome": "outside_calibrated_regime",
            "extraction_id": extraction.id,
            "alert_id": alert.id if alert else None,
            "deduplicated": True,
            "replayed": True,
        }

    if outcome == "auto_accept":
        result = _auto_accept(session, extraction)
    elif outcome == "audit_sample":
        result = _audit_sample(session, extraction)
    elif outcome == "review":
        result = _targeted_review(session, extraction)
    elif outcome == "conflict":
        result = _open_conflict_placeholder(session, extraction)
    else:  # outside_calibrated_regime
        result = _outside_calibrated_regime(session, extraction, novelty_cluster_id or extraction.id)

    # Persist decision record in the same transaction (D2)
    dec_rec = DecisionRecord(
        id=str(uuid.uuid4()),
        extraction_id=extraction.id,
        outcome=outcome,
        envelope_id=envelope_id,
        decided_at=datetime.now(timezone.utc),
    )
    session.add(dec_rec)

    audit_append(
        session, actor="system:decision-engine", action=f"decision.route.{outcome}", subject=extraction.id,
    )
    session.flush()

    # P5-06-fix/FR-ANL-01 — "processed" means every field on the page has
    # reached a terminal decision outcome, checked (and, if true, emitted
    # exactly once) right here, not at triage-routing time. See
    # `backend.domain.page_lifecycle`'s module docstring. Imported locally
    # — `page_lifecycle` imports `VALID_ROUTING_OUTCOMES` from this
    # module, so a top-level import here would be circular.
    from backend.domain.page_lifecycle import mark_processed_if_terminal

    mark_processed_if_terminal(session, extraction.page_id)

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


def _outside_calibrated_regime(session: Session, extraction: Extraction, cluster_key: str) -> dict:
    """FR-CNF-14 — never auto-accepted at any calibrated confidence, and a
    *cluster* of novel pages raises ONE operational alert, not a stream of
    per-field review tasks (P3-02). No `ReviewTask` is created here,
    matching the PRD's explicit "not a stream of per-field tasks"
    requirement.

    Dedup is on `cluster_key` within `NOVELTY_CLUSTER_ALERT_DEDUP_WINDOW`:
    a second extraction from the same cluster inside the window is folded
    into the existing `OperationalAlert` (its id appended to
    `extraction_ids`); outside the window, or for a new cluster, a fresh
    alert opens. Backend does not compute cluster membership itself — the
    caller supplies `cluster_key` (see `route`'s docstring).
    """
    cutoff = datetime.now(timezone.utc) - NOVELTY_CLUSTER_ALERT_DEDUP_WINDOW
    existing = session.execute(
        select(OperationalAlert)
        .where(
            OperationalAlert.kind == "outside_calibrated_regime",
            OperationalAlert.cluster_key == cluster_key,
            OperationalAlert.opened_at >= cutoff,
        )
        .order_by(OperationalAlert.opened_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if existing is not None:
        if extraction.id not in existing.extraction_ids:
            existing.extraction_ids = [*existing.extraction_ids, extraction.id]
        session.flush()
        return {"outcome": "outside_calibrated_regime", "extraction_id": extraction.id, "alert_id": existing.id, "deduplicated": True}

    alert = OperationalAlert(
        kind="outside_calibrated_regime", cluster_key=cluster_key, extraction_ids=[extraction.id],
    )
    session.add(alert)
    session.flush()
    return {"outcome": "outside_calibrated_regime", "extraction_id": extraction.id, "alert_id": alert.id, "deduplicated": False}
