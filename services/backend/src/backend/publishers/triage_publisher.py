"""Publishes to TEXT_QUEUE / MAP_QUEUE with the pinned WorkEnvelope.
TODO: FR-TRI-05/09.
"""
from observability import emit


def publish_to_lane(queue: str, page: dict, work_envelope: dict, trace_id: str) -> dict:
    # TODO: wire actual transport once the broker is chosen ([DESIGN CHOICE]).
    return emit(queue=queue, producer="triage", payload=page, work_envelope=work_envelope, trace_id=trace_id)
