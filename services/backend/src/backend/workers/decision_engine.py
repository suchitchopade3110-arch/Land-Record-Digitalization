"""Consumes DECISION_QUEUE. Routes each Extraction to exactly one of
auto-accept / audit-sample / review / conflict / outside-regime, per
Architecture §15 (5 outcomes, not 3). FR-CNF-04, FR-CFL-01.

Reads `routing_outcome` already set by Tharun's confidence/novelty engine
(M8) — this worker does not compute confidence/novelty itself, only acts
on it (`backend.domain.decision.route` is the actual logic; this module is
the queue-consumer shell around it).
"""
from __future__ import annotations

from observability import traced_consumer
from sqlalchemy.orm import Session

from backend.domain.decision import route


@traced_consumer
def handle(message: dict, session: Session) -> dict:
    """`message["payload"]` is an `Extraction`-shaped dict per
    `contracts/schemas/extraction.schema.json` with `routing_outcome`
    already set (API-Contracts §5's Confidence/Novelty → Decision hop).
    `session` is injected by the worker runner
    (`services/backend/src/backend/main.py`'s consumer loop), not opened
    here, so one DB transaction spans "read the message" through "commit
    the routing decision and its audit event."""
    extraction_id = message["payload"]["id"]
    result = route(session, extraction_id)
    session.commit()
    return result
