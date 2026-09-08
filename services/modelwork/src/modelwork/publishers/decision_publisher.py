"""Publishes Extraction (with calibrated_confidence, novelty_score,
routing_outcome set) to DECISION_QUEUE. TODO: FR-CNF-04."""
from observability import emit


def publish(extraction: dict, work_envelope: dict, trace_id: str) -> dict:
    return emit(queue="DECISION_QUEUE", producer="confidence-novelty", payload=extraction, work_envelope=work_envelope, trace_id=trace_id)
