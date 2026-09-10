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

from backend.domain.decision import route
from backend.models.entities import Extraction


@traced_consumer
def handle(message: dict, session: Session) -> dict:
    """`message["payload"]` is an `Extraction`-shaped dict per
    `contracts/schemas/extraction.schema.json` with `routing_outcome`
    already set (API-Contracts §5's Confidence/Novelty → Decision hop).
    `session` is injected by the worker runner
    (`services/backend/src/backend/main.py`'s consumer loop), not opened
    here, so one DB transaction spans "read the message" through "commit
    the routing decision and its audit event."""
    payload = message.get("payload", {})
    extraction_id = payload.get("id")
    if not extraction_id:
        raise ValueError("DecisionEnvelope missing required payload.id")

    extraction = session.get(Extraction, extraction_id)
    if extraction is not None:
        if "routing_outcome" in payload and payload["routing_outcome"] is not None:
            extraction.routing_outcome = payload["routing_outcome"]
        if "calibrated_confidence" in payload and payload["calibrated_confidence"] is not None:
            extraction.calibrated_confidence = payload["calibrated_confidence"]

    novelty_cluster_id = payload.get("novelty_cluster_id") or message.get("novelty_cluster_id")
    result = route(session, extraction_id, novelty_cluster_id=novelty_cluster_id)
    session.commit()
    return result
