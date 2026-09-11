"""Field-level feature attribution formatting for review explanation (FR-REV-03, FR-REV-15).

Translates calibrator model feature contributions into deterministic, stably ordered
attribution items and human-readable explanation text for ReviewTask.reason.
Novelty signals are strictly excluded from confidence attributions (FR-CNF-14).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from modelwork.domain.calibration.calibrator import CalibratorFeatures


@dataclass(frozen=True)
class FeatureAttributionItem:
    """Individual feature attribution entry for reviewer transparency (FR-REV-15)."""

    feature_name: str
    feature_value: Any
    contribution: float

    def __post_init__(self) -> None:
        if not self.feature_name:
            raise ValueError("feature_name must be a non-empty string")
        if not math.isfinite(self.contribution):
            raise ValueError(f"contribution must be a finite real number, got {self.contribution}")


def extract_feature_value(features: CalibratorFeatures, feature_name: str) -> Any:
    """Extract corresponding raw or derived value from CalibratorFeatures for an attribution key."""
    if feature_name == "token_confidence":
        return features.token_confidence
    elif feature_name == "layout_certainty":
        return features.layout_certainty
    elif feature_name == "normalization_confidence":
        return features.normalization_confidence
    elif feature_name == "layout_is_missing":
        return 1.0 if features.layout_certainty is None else 0.0
    elif feature_name == "normalization_is_missing":
        return 1.0 if features.normalization_confidence is None else 0.0
    elif feature_name == "is_handwritten":
        return 1.0 if features.print_or_handwriting == "handwritten" else 0.0
    elif feature_name == "legibility_score":
        return features.legibility_band
    elif feature_name == "validator_has_failure":
        return any(v.lower() == "fail" for v in features.validator_outcomes.values())
    elif feature_name == "has_validators":
        return 1.0 if bool(features.validator_outcomes) else 0.0
    elif feature_name == "validator_pass_ratio":
        if not features.validator_outcomes:
            return 1.0
        passes = sum(1 for v in features.validator_outcomes.values() if v.lower() == "pass")
        return round(passes / len(features.validator_outcomes), 4)
    elif feature_name.startswith("field_class="):
        target = feature_name.split("=", 1)[1]
        return 1.0 if features.field_class == target else 0.0
    elif feature_name.startswith("script="):
        target = feature_name.split("=", 1)[1]
        return 1.0 if features.script == target else 0.0
    elif feature_name.startswith("doc_type="):
        target = feature_name.split("=", 1)[1]
        return 1.0 if features.doc_type == target else 0.0
    return getattr(features, feature_name, None)


def format_feature_attributions(
    features: CalibratorFeatures,
    attributions: dict[str, float] | None,
) -> list[FeatureAttributionItem]:
    """Format and sort calibrator feature contributions into deterministic attribution items (FR-REV-15).

    Deterministic Ordering Policy:
    Sorted primarily by descending absolute contribution magnitude (|contribution|),
    and secondarily by ascending feature_name for stable tie-breaking.
    Strictly omits novelty metrics (FR-CNF-14).
    """
    if not attributions:
        return []

    items: list[FeatureAttributionItem] = []
    for name, contrib in attributions.items():
        if not isinstance(contrib, (int, float)) or not math.isfinite(contrib):
            raise ValueError(f"Attribution contribution for {name} must be a finite number, got {contrib}")
        val = extract_feature_value(features, name)
        items.append(
            FeatureAttributionItem(
                feature_name=name,
                feature_value=val,
                contribution=float(contrib),
            )
        )

    # Sort stably: largest magnitude effect first, then alphabetical by feature name
    items.sort(key=lambda item: (-abs(item.contribution), item.feature_name))
    return items


def format_attribution_reason(
    attributions: list[FeatureAttributionItem] | dict[str, float] | None,
    features: CalibratorFeatures | None = None,
    max_features: int = 3,
) -> str:
    """Format dominant calibrator features into a concise reason string for ReviewTask.reason (FR-REV-03/15).

    Used when a field is routed to review due to low or borderline calibrated confidence.
    """
    if not attributions:
        return "calibrator: low confidence"

    if isinstance(attributions, dict):
        if features is None:
            # Sort by absolute contribution and name directly from dict
            sorted_entries = sorted(attributions.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
            parts = [f"{k} ({v:+.2f})" for k, v in sorted_entries[:max_features]]
            return f"calibrator: {', '.join(parts)}"
        formatted_items = format_feature_attributions(features, attributions)
    else:
        formatted_items = attributions

    if not formatted_items:
        return "calibrator: low confidence"

    top_items = formatted_items[:max_features]
    parts = [f"{item.feature_name} ({item.contribution:+.2f})" for item in top_items]
    return f"calibrator: {', '.join(parts)}"
