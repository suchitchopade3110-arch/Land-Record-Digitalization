"""Legibility scoring, legibility_band stratification key, and threshold breach evaluation.
Requirements: FR-TRI-01 (quality_score + rescan breach), FR-TRI-10 (legibility_band).

SOURCE-OF-TRUTH CONTRACTS:
- contracts/schemas/page.schema.json:
  - quality_score: number | null
  - legibility_band: enum ["good", "marginal", "poor", null]
- contracts/schemas/model_version.schema.json: module "triage_classifier"
- services/backend/src/backend/domain/rescan.py: open_from_threshold_breach

NON-NEGOTIABLE SAFETY:
- quality_score must be finite and bounded to [0.0, 1.0].
- legibility_band must strictly use only canonical enum values ("good", "marginal", "poor").
- No magic production thresholds: policy is fully injectable via LegibilityPolicy.
- Deterministic test double provided for unit/contract tests; no fake production ML claimed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

LegibilityBand = Literal["good", "marginal", "poor"]
VALID_LEGIBILITY_BANDS: frozenset[str] = frozenset({"good", "marginal", "poor"})

CANONICAL_BREACH_REASON: str = "quality_below_threshold"
ILLEGIBLE_REGION_REASON: str = "illegible_region"


@dataclass(frozen=True)
class LegibilityPolicy:
    """Configurable legibility evaluation and rescan threshold policy (FR-TRI-01, FR-TRI-10).

    EXPLICIT CONFIGURATION / POLICY BOUNDARY:
    Thresholds are injected via configuration, never hardcoded as magic production constants.
    """

    poor_threshold: float = 0.40
    good_threshold: float = 0.70
    rescan_threshold: float = 0.40
    breach_reason_code: str = CANONICAL_BREACH_REASON

    def __post_init__(self) -> None:
        for name, val in [
            ("poor_threshold", self.poor_threshold),
            ("good_threshold", self.good_threshold),
            ("rescan_threshold", self.rescan_threshold),
        ]:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError(f"{name} must be a finite real number, got {val}")
            if not (0.0 <= float(val) <= 1.0):
                raise ValueError(f"{name} must be in [0.0, 1.0], got {val}")

        if self.poor_threshold > self.good_threshold:
            raise ValueError(
                f"poor_threshold ({self.poor_threshold}) cannot exceed good_threshold ({self.good_threshold})"
            )
        if self.rescan_threshold > self.good_threshold:
            raise ValueError(
                f"rescan_threshold ({self.rescan_threshold}) cannot exceed good_threshold ({self.good_threshold})"
            )
        if not self.breach_reason_code or not isinstance(self.breach_reason_code, str):
            raise ValueError("breach_reason_code must be a non-empty string")

    def classify_band(self, quality_score: float) -> LegibilityBand:
        """Classify quality score into canonical legibility band (FR-TRI-10)."""
        if not math.isfinite(quality_score):
            raise ValueError(f"quality_score must be a finite real number, got {quality_score}")
        if not (0.0 <= quality_score <= 1.0):
            raise ValueError(f"quality_score must be in [0.0, 1.0], got {quality_score}")

        if quality_score < self.poor_threshold:
            return "poor"
        if quality_score < self.good_threshold:
            return "marginal"
        return "good"

    def is_threshold_breached(self, quality_score: float) -> bool:
        """Check if quality score breaches the rescan threshold (FR-TRI-01)."""
        if not math.isfinite(quality_score):
            raise ValueError(f"quality_score must be a finite real number, got {quality_score}")
        return quality_score < self.rescan_threshold

    def evaluate(self, quality_score: float) -> tuple[LegibilityBand, bool, str | None]:
        """Evaluate quality_score returning (band, is_breached, reason_code)."""
        band = self.classify_band(quality_score)
        breached = self.is_threshold_breached(quality_score)
        reason = self.breach_reason_code if breached else None
        return band, breached, reason


@dataclass(frozen=True)
class LegibilityResult:
    """Structured output of legibility classification (FR-TRI-01, FR-TRI-10)."""

    quality_score: float
    legibility_band: str
    is_breached: bool
    reason_code: str | None = None
    model_version: str = "triage_classifier_v1"

    def __post_init__(self) -> None:
        if not isinstance(self.quality_score, (int, float)) or not math.isfinite(self.quality_score):
            raise ValueError(f"quality_score must be a finite real number, got {self.quality_score}")
        if not (0.0 <= float(self.quality_score) <= 1.0):
            raise ValueError(f"quality_score must be in [0.0, 1.0], got {self.quality_score}")
        if self.legibility_band not in VALID_LEGIBILITY_BANDS:
            raise ValueError(
                f"legibility_band must be one of {sorted(VALID_LEGIBILITY_BANDS)}, got {self.legibility_band!r}"
            )
        if self.is_breached and not self.reason_code:
            raise ValueError("reason_code must be populated when is_breached is True")


class LegibilityScorer(Protocol):
    """Protocol for legibility scoring implementations."""

    def score(
        self,
        page_image: bytes,
        policy: LegibilityPolicy | None = None,
    ) -> LegibilityResult:
        """Score legibility of page image and return structured LegibilityResult."""
        ...


@dataclass(frozen=True)
class DeterministicLegibilityScorer:
    """Deterministic test double and baseline policy implementation of LegibilityScorer.

    COMPATIBLE IMPLEMENTATION POLICY / TEST-ONLY DOUBLE:
    Allows reproducible unit/contract testing without asserting unverified ML model weights.
    """

    scoring_fn: Callable[[bytes], float] | None = None
    fixed_score: float | None = None
    model_version: str = "triage_classifier_v1"
    policy: LegibilityPolicy = field(default_factory=LegibilityPolicy)

    def score(
        self,
        page_image: bytes,
        policy: LegibilityPolicy | None = None,
    ) -> LegibilityResult:
        active_policy = policy or self.policy

        if self.fixed_score is not None:
            raw_score = float(self.fixed_score)
        elif self.scoring_fn is not None:
            raw_score = float(self.scoring_fn(page_image))
        else:
            # Deterministic baseline heuristic from bytes content
            if not page_image:
                raw_score = 0.0
            else:
                sample_bytes = page_image[:min(len(page_image), 4096)]
                avg_byte = sum(sample_bytes) / (len(sample_bytes) * 255.0)
                # Bounded deterministic score derived from sample variance
                variance = sum((b / 255.0 - avg_byte) ** 2 for b in sample_bytes) / len(sample_bytes)
                raw_score = min(1.0, max(0.0, math.sqrt(variance) * 2.0))

        if not math.isfinite(raw_score):
            raise ValueError(f"Computed quality_score is not finite: {raw_score}")

        bounded_score = min(1.0, max(0.0, raw_score))
        band, breached, reason = active_policy.evaluate(bounded_score)

        return LegibilityResult(
            quality_score=bounded_score,
            legibility_band=band,
            is_breached=breached,
            reason_code=reason,
            model_version=self.model_version,
        )


def score_legibility(
    page_image: bytes,
    policy: LegibilityPolicy | None = None,
    scorer: LegibilityScorer | None = None,
) -> dict[str, Any]:
    """Domain entry point for legibility scoring (FR-TRI-01/10).

    Returns a dict representation matching the contract expectations:
    {"quality_score": float, "legibility_band": str, "is_breached": bool, "reason_code": str | None, "model_version": str}
    """
    active_scorer = scorer or DeterministicLegibilityScorer()
    res = active_scorer.score(page_image, policy=policy)
    return {
        "quality_score": res.quality_score,
        "legibility_band": res.legibility_band,
        "is_breached": res.is_breached,
        "reason_code": res.reason_code,
        "model_version": res.model_version,
    }
