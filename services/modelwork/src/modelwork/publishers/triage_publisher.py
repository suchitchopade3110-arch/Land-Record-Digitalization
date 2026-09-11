"""Publishes classified Page to TRIAGE_QUEUE with producer='triage'.
Contract: contracts/asyncapi/triage-queue.yaml
Payload: contracts/schemas/page.schema.json

Next consumer: Backend triage_router (services/backend/src/backend/workers/triage_router.py).
"""
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
    page: dict[str, Any],
    trace_id: str,
    work_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Publish a classified Page to TRIAGE_QUEUE with producer='triage'."""
    return emit(
        queue="TRIAGE_QUEUE",
        producer="triage",
        payload=page,
        work_envelope=work_envelope,
        trace_id=trace_id,
    )
