"""Consumes DECISION_QUEUE. Routes each Extraction to exactly one of
auto-accept / audit-sample / review / conflict / outside-regime, per
Architecture §15 (5 outcomes, not 3). FR-CNF-04, FR-CFL-01.

Reads `routing_outcome` already set by Model Work confidence/novelty engine
(M8) — this worker does not compute confidence/novelty itself, only acts
on it (`backend.domain.decision.route` is the actual logic; this module is
the queue-consumer shell around it).
"""
from __future__ import annotations

from observability import traced_consumer
from sqlalchemy.orm import Session

from backend.domain.audit_log import record_payload_mismatch
from backend.domain.decision import (
    ExtractionNotFoundError,
    PayloadMismatchError,
    route,
)
from backend.models.entities import Extraction


@traced_consumer
def handle(message: dict, session: Session) -> dict:
    """`message["payload"]` is an `Extraction`-shaped dict per
    `contracts/schemas/extraction.schema.json` with `routing_outcome`
    already set (API-Contracts §5's Confidence/Novelty → Decision hop).
    `session` is injected by the worker runner
    (`services/backend/src/backend/main.py`'s consumer loop), not opened
    here, so one DB transaction spans "read the message" through "commit
    the routing decision and its audit event."

    D1-A: Database is source of truth. Upstream owners write their own columns
    directly to Postgres. Decision engine does not overwrite DB columns from payload.
    If payload contradicts DB state, an audit mismatch is recorded and
    PayloadMismatchError is raised.
    """
    payload = message.get("payload", {})
    extraction_id = payload.get("id")
    if not extraction_id:
        raise ValueError("DecisionEnvelope missing required payload.id")

    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise ExtractionNotFoundError(f"Extraction {extraction_id} not found in database")

    # Verify payload matches DB state if provided (D1-A)
    mismatched_fields: list[str] = []
    if (
        "routing_outcome" in payload
        and payload["routing_outcome"] is not None
        and payload["routing_outcome"] != extraction.routing_outcome
    ):
        mismatched_fields.append("routing_outcome")
    if (
        "calibrated_confidence" in payload
        and payload["calibrated_confidence"] is not None
        and payload["calibrated_confidence"] != extraction.calibrated_confidence
    ):
        mismatched_fields.append("calibrated_confidence")

    if mismatched_fields:
        record_payload_mismatch(
            session,
            extraction_id=extraction_id,
            field_names=mismatched_fields,
        )
        # Commit the audit entry now, before raising — the caller (the
        # worker runner) rolls back this same session on any handler
        # exception (ack-after-commit, D1), which would otherwise erase
        # the very audit trail this mismatch check exists to produce.
        # Nothing else is pending in this transaction yet (the extraction
        # fetch above was read-only), so this only commits the audit row.
        session.commit()
        raise PayloadMismatchError(
            f"Payload values for {mismatched_fields} do not match database state for extraction {extraction_id}"
        )

    novelty_cluster_id = payload.get("novelty_cluster_id") or message.get("novelty_cluster_id")
    work_envelope = message.get("work_envelope") or {}
    envelope_id = work_envelope.get("envelope_id") or message.get("envelope_id")

    return route(
        session,
        extraction_id,
        novelty_cluster_id=novelty_cluster_id,
        envelope_id=envelope_id,
    )

