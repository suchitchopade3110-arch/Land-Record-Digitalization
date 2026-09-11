"""Learning data collection boundary from reviewer corrections (FR-LRN-01, FR-LRN-07, FR-LRN-11).

Ingests and validates reviewer corrections from LEARNING_LOOP_QUEUE envelopes or
correction records into structured, page-isolated learning examples for the learning loop.
Enforces stream preservation, historical provenance, and page-level leakage isolation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from modelwork.domain.calibration.calibrator import CalibratorFeatures, CorrectionLabel

# Authoritative streams defined by contracts/schemas/correction.schema.json
VALID_CORRECTION_STREAMS = frozenset({"routed", "audit", "downstream", "legacy_digital"})


@dataclass(frozen=True)
class LearningExample:
    """Structured learning record preserving full historical provenance and page isolation (FR-LRN-01/07/11)."""

    correction_id: str
    extraction_id: str
    crop_uri: str
    predicted: str
    corrected: str
    edit_distance: int
    is_correct: bool
    actor: str
    model_version: str
    config_version: str
    source_stream: str
    source_page_digest: str
    reliability_weight: float | None = None
    trace_id: str = ""

    def __post_init__(self) -> None:
        if not self.correction_id:
            raise ValueError("correction_id must be a non-empty string")
        if not self.extraction_id:
            raise ValueError("extraction_id must be a non-empty string")
        if not self.source_page_digest:
            raise ValueError("source_page_digest must be a non-empty string (FR-LRN-11)")
        if not self.model_version:
            raise ValueError("model_version must be a non-empty string")
        if not self.config_version:
            raise ValueError("config_version must be a non-empty string")
        if self.source_stream not in VALID_CORRECTION_STREAMS:
            raise ValueError(
                f"source_stream {self.source_stream!r} is not one of {sorted(VALID_CORRECTION_STREAMS)}"
            )
        if self.edit_distance < 0:
            raise ValueError(f"edit_distance must be non-negative, got {self.edit_distance}")
        if self.reliability_weight is not None:
            if not math.isfinite(self.reliability_weight) or self.reliability_weight < 0.0:
                raise ValueError(f"reliability_weight must be a non-negative finite real, got {self.reliability_weight}")


def collect_learning_example(
    envelope_or_correction: dict[str, Any],
    trace_id: str = "",
) -> LearningExample:
    """Collect and validate a reviewer correction into a structured LearningExample.

    Accepts either a full LearningLoopEnvelope (contracts/asyncapi/learning-loop-queue.yaml)
    or a raw Correction payload (contracts/schemas/correction.schema.json).
    Preserves historical provenance and strictly enforces page isolation.
    """
    if not isinstance(envelope_or_correction, dict) or not envelope_or_correction:
        raise ValueError("envelope_or_correction must be a non-empty dictionary")

    # Unwrap LearningLoopEnvelope payload if nested
    if "payload" in envelope_or_correction and isinstance(envelope_or_correction["payload"], dict):
        correction_dict = envelope_or_correction["payload"]
        env_trace = envelope_or_correction.get("trace_id", "")
        active_trace = trace_id or env_trace
    else:
        correction_dict = envelope_or_correction
        active_trace = trace_id

    corr_id = correction_dict.get("id") or correction_dict.get("correction_id")
    if not corr_id or not isinstance(corr_id, str):
        raise ValueError("Missing or invalid 'id' / 'correction_id' in correction")

    ext_id = correction_dict.get("extraction_id")
    if not ext_id or not isinstance(ext_id, str):
        raise ValueError("Missing or invalid 'extraction_id' in correction")

    crop_uri = correction_dict.get("crop_uri")
    if not crop_uri or not isinstance(crop_uri, str):
        raise ValueError("Missing or invalid 'crop_uri' in correction")

    predicted = correction_dict.get("predicted", "")
    corrected = correction_dict.get("corrected", "")
    if predicted is None:
        predicted = ""
    if corrected is None:
        corrected = ""

    dist = correction_dict.get("edit_distance")
    if dist is None:
        # Fallback to computing edit distance if omitted without cross-service import
        a, b = str(predicted), str(corrected)
        if a == b:
            distance = 0
        elif not a:
            distance = len(b)
        elif not b:
            distance = len(a)
        else:
            prev = list(range(len(b) + 1))
            for i, ca in enumerate(a, start=1):
                curr = [i] + [0] * len(b)
                for j, cb in enumerate(b, start=1):
                    cost = 0 if ca == cb else 1
                    curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
                prev = curr
            distance = prev[-1]
    else:
        if not isinstance(dist, int) or dist < 0:
            raise ValueError(f"edit_distance must be a non-negative integer, got {dist}")
        distance = dist

    actor = correction_dict.get("actor")
    if not actor or not isinstance(actor, str):
        raise ValueError("Missing or invalid 'actor' in correction")

    model_ver = correction_dict.get("model_version")
    if not model_ver or not isinstance(model_ver, str):
        raise ValueError("Missing required provenance 'model_version' in correction")

    config_ver = correction_dict.get("config_version")
    if not config_ver or not isinstance(config_ver, str):
        raise ValueError("Missing required provenance 'config_version' in correction")

    stream = correction_dict.get("stream") or correction_dict.get("source_stream")
    if not stream or stream not in VALID_CORRECTION_STREAMS:
        raise ValueError(f"Missing or invalid stream: {stream!r} (must be in {sorted(VALID_CORRECTION_STREAMS)})")

    digest = correction_dict.get("source_page_digest")
    if not digest or not isinstance(digest, str):
        raise ValueError("Missing required provenance 'source_page_digest' in correction (FR-LRN-11)")

    rel_weight = correction_dict.get("reliability_weight")
    weight = float(rel_weight) if rel_weight is not None else None

    is_correct = (distance == 0) or (predicted == corrected)

    return LearningExample(
        correction_id=str(corr_id),
        extraction_id=str(ext_id),
        crop_uri=str(crop_uri),
        predicted=str(predicted),
        corrected=str(corrected),
        edit_distance=distance,
        is_correct=is_correct,
        actor=str(actor),
        model_version=str(model_ver),
        config_version=str(config_ver),
        source_stream=str(stream),
        source_page_digest=str(digest),
        reliability_weight=weight,
        trace_id=str(active_trace),
    )


def learning_example_to_correction_label(
    example: LearningExample,
    features: CalibratorFeatures,
    stratum: str = "",
) -> CorrectionLabel:
    """Convert a LearningExample and its associated CalibratorFeatures into a CorrectionLabel.

    Enables leakage-guarded data partitioning (partition_training_and_evaluation)
    and calibrator training/evaluation with source_page_digest preserved (FR-LRN-01/11).
    """
    canonical_stratum = stratum or features.canonical_stratum()
    return CorrectionLabel(
        extraction_id=example.extraction_id,
        is_correct=example.is_correct,
        features=features,
        stratum=canonical_stratum,
        source_page_digest=example.source_page_digest,
    )
