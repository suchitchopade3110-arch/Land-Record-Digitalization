"""GET /models/{module}/active?stratum={stratum} — FR-LRN-04.
OpenAPI contract: contracts/openapi/model-registry.*.yaml

Returns the currently promoted version only — the ONE place "current" is
resolved. Once pinned into a WorkEnvelope, nothing downstream calls this
again for that page (API-Contracts §4.2, FR-TRI-09).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["model-registry"])

# Canonical valid modules per contracts/schemas/model_version.schema.json
VALID_MODULES: frozenset[str] = frozenset({
    "printed_ocr",
    "hwr",
    "calibrator",
    "novelty_detector",
    "triage_classifier",
})


@dataclass(frozen=True)
class ActiveModelRecord:
    """In-process representation of an active model version."""

    model_version: str
    adapter_ref: str | None = None


# Deterministic P0 in-process baseline registry.
# NOTE: This provides the initial deterministic model versions required by
# WorkEnvelope pinning (FR-TRI-09) during P0 development before persistent
# ModelVersion storage and the automated promotion gate (FR-LRN-04 / FR-LRN-08)
# are implemented. These values represent baseline bootstrap versions, not models
# that have completed full production training loops.
_ACTIVE_PROMOTED_MODELS: dict[str, ActiveModelRecord] = {
    "printed_ocr": ActiveModelRecord(model_version="printed_ocr_v1"),
    "hwr": ActiveModelRecord(model_version="hwr_v1"),
    "calibrator": ActiveModelRecord(model_version="calibrator_v1"),
    "novelty_detector": ActiveModelRecord(model_version="novelty_detector_v1"),
    "triage_classifier": ActiveModelRecord(model_version="triage_classifier_v1"),
}

# In-process mapping for writer cluster adapters: (module, writer_cluster_id) -> adapter_ref.
# Per-writer adaptation training is P1 scope (FR-OCR-08); empty by default in P0.
_WRITER_ADAPTERS: dict[tuple[str, str], str] = {}


@router.get("/models/{module}/active")
def get_active_model(
    module: str,
    writer_cluster_id: str | None = None,
    stratum: str | None = None,
) -> dict[str, Any]:
    """GET /models/{module}/active

    Read the currently promoted model version for a module.
    Returns HTTP 200 with model_version and adapter_ref if active.
    Returns HTTP 404 if the module is unknown or has no active promoted model.
    """
    if module not in VALID_MODULES or module not in _ACTIVE_PROMOTED_MODELS:
        raise HTTPException(
            status_code=404,
            detail=f"No promoted model for module '{module}'",
        )

    record = _ACTIVE_PROMOTED_MODELS[module]

    adapter_ref: str | None = None
    if writer_cluster_id:
        adapter_ref = _WRITER_ADAPTERS.get((module, writer_cluster_id), record.adapter_ref)
    else:
        adapter_ref = record.adapter_ref

    # Note: stratum parameter is accepted per contract specification.
    # Stratum-specific calibrator routing is deferred until stratum-keyed
    # calibrator checkpoints are trained; the active module-level model version
    # is returned without speculative selection.

    return {
        "model_version": record.model_version,
        "adapter_ref": adapter_ref,
    }
