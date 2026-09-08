"""Publishes to REVIEW_QUEUE / CONFLICT_QUEUE / PUBLICATION_QUEUE.
TODO: FR-CNF-04, FR-CFL-01, FR-PUB-01.
"""
from observability import emit


def publish_outcome(queue: str, payload: dict, work_envelope: dict, trace_id: str) -> dict:
    return emit(queue=queue, producer="decision", payload=payload, work_envelope=work_envelope, trace_id=trace_id)
