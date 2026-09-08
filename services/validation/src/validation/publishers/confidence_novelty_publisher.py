"""Publishes ValidationResult[] + dedup match candidates to
CONFIDENCE_NOVELTY_QUEUE. TODO: FR-CNF-01."""
from observability import emit


def publish(validation_results: list[dict], dedup_candidates: list[dict], work_envelope: dict, trace_id: str) -> dict:
    return emit(
        queue="CONFIDENCE_NOVELTY_QUEUE",
        producer="validation",
        payload={"validation_results": validation_results, "dedup_match_candidates": dedup_candidates},
        work_envelope=work_envelope,
        trace_id=trace_id,
    )
