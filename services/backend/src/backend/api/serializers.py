"""Field masking enforced at the serialization layer, not the UI — TODO: FR-SEC-02.

Three places a masked field's personal data must never leak (Architecture §18):
  1. the current value
  2. the provenance raw_value
  3. the linked source crop
Masking only the current value leaves two unmasked copies — the specific bug
the PRD calls out. Also: ReviewTask.source_stream must never leave this layer
(FR-REV-11).
"""
from typing import Any


def mask_extraction_for_role(extraction: dict[str, Any], role: str) -> dict[str, Any]:
    # TODO: FR-SEC-02 — mask value, provenance.raw_value, and crop_uri together,
    # never independently, based on `role`.
    raise NotImplementedError("TODO: FR-SEC-02 not implemented")


def strip_review_task_internals(review_task: dict[str, Any]) -> dict[str, Any]:
    # TODO: FR-REV-11 — drop source_stream before this ever reaches a client.
    raise NotImplementedError("TODO: FR-REV-11 not implemented")
