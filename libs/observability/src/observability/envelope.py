"""Queue message envelope helpers — the shape defined in
API-Contracts-and-Interfaces.md §1 ([DESIGN CHOICE], applied uniformly across
every queue in contracts/asyncapi/*.yaml).

TODO: wire real publish/consume once the message broker is chosen
([DESIGN CHOICE] in Team-Split §skeleton — Redis Streams suggested for a
4-person P0 build). This module only shapes and validates the envelope;
transport is intentionally not implemented here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

Producer = Literal[
    "ingest", "triage", "text-lane", "map-lane", "assembly", "normalize",
    "validation", "entity-res", "confidence-novelty", "decision", "review",
    "conflict", "publication", "learning-loop",
]

REQUIRED_ENVELOPE_KEYS = {"message_id", "trace_id", "emitted_at", "producer", "payload"}


def emit(
    queue: str,
    producer: Producer,
    payload: dict[str, Any],
    work_envelope: dict[str, Any] | None,
    trace_id: str,
) -> dict[str, Any]:
    """Build a standard envelope. Does not publish — caller's
    services/<name>/publishers/*.py owns the actual transport call.
    """
    return {
        "message_id": str(uuid.uuid4()),
        "trace_id": trace_id,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "producer": producer,
        "work_envelope": work_envelope,
        "payload": payload,
        "_queue": queue,
    }


def validate_envelope(message: dict[str, Any]) -> None:
    """Raise ValueError if message is missing a required envelope key.

    TODO: extend to validate `payload` against the relevant
    contracts/schemas/*.json once contracts/generated/python is wired in.
    """
    missing = REQUIRED_ENVELOPE_KEYS - message.keys()
    if missing:
        raise ValueError(f"envelope missing required keys: {sorted(missing)}")
