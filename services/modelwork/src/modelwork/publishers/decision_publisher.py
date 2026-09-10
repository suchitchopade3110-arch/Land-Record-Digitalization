"""Publishes Extraction (with calibrated_confidence, novelty_score,
routing_outcome set) to DECISION_QUEUE (FR-CNF-04)."""
from __future__ import annotations

from typing import Any

try:
    from observability import emit
except ImportError:  # pragma: no cover
    import uuid
    from datetime import datetime, timezone

    def emit(
        queue: str,
        producer: str,
        payload: dict[str, Any],
        work_envelope: dict[str, Any] | None,
        trace_id: str,
    ) -> dict[str, Any]:
        return {
            "message_id": str(uuid.uuid4()),
            "trace_id": trace_id,
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "producer": producer,
            "work_envelope": work_envelope,
            "payload": payload,
            "_queue": queue,
        }


def publish(
    extraction: dict[str, Any],
    work_envelope: dict[str, Any],
    trace_id: str,
    novelty_cluster_id: str | None = None,
) -> dict[str, Any]:
    """Publish an Extraction to DECISION_QUEUE with WorkEnvelope and trace_id."""
    envelope = emit(
        queue="DECISION_QUEUE",
        producer="confidence-novelty",
        payload=extraction,
        work_envelope=work_envelope,
        trace_id=trace_id,
    )
    if novelty_cluster_id is not None:
        envelope["novelty_cluster_id"] = novelty_cluster_id
    return envelope
