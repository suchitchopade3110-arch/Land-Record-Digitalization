"""Triage Novelty Pre-Score (FR-CNF-14).
Evaluates initial stratum familiarity and layout novelty at triage time before extraction.

SOURCE-OF-TRUTH CONTRACTS:
- contracts/schemas/page.schema.json:
  - novelty_score: number | null ("FR-CNF-14, pre-score at triage; refined post-extraction")
- services/modelwork/src/modelwork/domain/calibration/novelty.py:
  - NoveltyDetector, CentroidDistanceNoveltyModel, NoveltyFeatures, NoveltyPolicy

STRICT CONFIDENCE SEPARATION:
Confidence metrics do not exist at triage stage. Triage novelty pre-score strictly
evaluates initial stratum identity (script, doc_type, legibility_band, writer_cluster_id)
against calibrated reference stratum prototypes.
"""
from __future__ import annotations

import math
from typing import Any

from modelwork.domain.calibration.novelty import (
    CentroidDistanceNoveltyModel,
    NoveltyDetector,
    NoveltyFeatures,
    NoveltyPolicy,
    NoveltyResult,
)


def compute_triage_novelty_prescore(
    *,
    doc_type: str | None,
    script: str | None,
    legibility_band: str | None,
    writer_cluster_id: str | None,
    detector: NoveltyDetector | None = None,
    policy: NoveltyPolicy | None = None,
    embedding: list[float] | None = None,
) -> float | None:
    """Compute triage novelty pre-score (FR-CNF-14).

    Returns a finite float in [0.0, 1.0] or None.
    """
    active_detector = detector or CentroidDistanceNoveltyModel()
    active_policy = policy or NoveltyPolicy()

    features = NoveltyFeatures(
        embedding=embedding,
        doc_type=doc_type or "",
        script=script or "",
        legibility_band=legibility_band or "good",
        writer_cluster_id=writer_cluster_id or "",
        field_class="triage_page",
        print_or_handwriting="printed",
    )

    result: NoveltyResult = active_detector.evaluate_novelty(features, policy=active_policy)
    score = result.novelty_score

    if not math.isfinite(score):
        raise ValueError(f"Computed novelty_score is not finite: {score}")

    return min(1.0, max(0.0, float(score)))
