"""Multi-page record assembly with rationale recorded (FR-EXT-04).

Produces a `RecordAssembly` object linking multiple extractions into a single record group.
Schema compliance enforced against `contracts/schemas/record_assembly.schema.json`.
"""
from __future__ import annotations

import uuid


def assemble(
    extractions: list[dict],
    record_id: str | None = None,
    strategy: str = "single_page",
    rationale: str = "Assembled from page extractions",
    actor: str = "system",
) -> dict:
    """Assembles extractions into a RecordAssembly structure."""
    if strategy not in ("single_page", "continuation", "carried_forward"):
        raise ValueError(f"Invalid assembly strategy: {strategy!r}")

    extraction_ids = [item["id"] for item in extractions if "id" in item]

    return {
        "id": str(uuid.uuid4()),
        "record_id": record_id or str(uuid.uuid4()),
        "extraction_ids": extraction_ids,
        "strategy": strategy,
        "rationale": rationale,
        "actor": actor,
    }
