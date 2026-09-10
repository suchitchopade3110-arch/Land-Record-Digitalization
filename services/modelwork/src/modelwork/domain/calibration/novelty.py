"""Novelty detection foundation and contract boundary (FR-CNF-14).

Computes novelty FIRST, from the recognition backbone's embedding distribution and
canonical stratum familiarity, strictly independent of confidence scores.
Gates the calibrated regime: high novelty or out-of-distribution inputs route to
'outside_calibrated_regime' and NEVER fall through to the pooled calibrator fallback.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from modelwork.domain.stratum import stratum_key


@dataclass(frozen=True)
class NoveltyPolicy:
    """Configurable novelty evaluation policy parameters (FR-CNF-14).

    EXPLICIT CONFIGURATION / POLICY BOUNDARY:
    Novelty threshold is derived from Config Service configuration, never hardcoded
    to magic business numbers (e.g. 0.90, 0.95, 0.98).
    """

    novelty_threshold: float = 0.50
    reject_unseen_strata: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.novelty_threshold):
            raise ValueError(f"novelty_threshold must be a finite real number, got {self.novelty_threshold}")
        if not (0.0 <= self.novelty_threshold <= 1.0):
            raise ValueError(
                f"novelty_threshold must be within [0.0, 1.0], got {self.novelty_threshold}"
            )

    def is_outside_regime(self, novelty_score: float, stratum_is_known: bool = True) -> bool:
        """Evaluate whether a novelty score or stratum familiarity breaches the regime boundary."""
        if self.reject_unseen_strata and not stratum_is_known:
            return True
        return novelty_score > self.novelty_threshold


@dataclass(frozen=True)
class NoveltyFeatures:
    """Feature representation for novelty evaluation (FR-CNF-14).

    STRICT CONFIDENCE SEPARATION:
    Novelty features capture representation embedding, stratum identity, layout certainty,
    and writer clustering. Crucially, raw token_confidence and calibrated_confidence
    are NEVER consumed or evaluated here. Novelty is strictly distinct from confidence.
    """

    embedding: list[float] | None = None
    stratum: str = ""
    field_class: str = ""
    script: str = ""
    doc_type: str = ""
    print_or_handwriting: str = ""
    legibility_band: str = "medium"
    writer_cluster_id: str = ""
    layout_certainty: float | None = None
    unconstrained_value: str | None = None
    raw_value: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.embedding is not None:
            if not isinstance(self.embedding, (list, tuple)) or len(self.embedding) == 0:
                raise ValueError("embedding must be a non-empty list of finite real numbers")
            for idx, val in enumerate(self.embedding):
                if not isinstance(val, (int, float)) or not math.isfinite(val):
                    raise ValueError(f"embedding[{idx}] must be a finite real number, got {val}")

        if self.layout_certainty is not None:
            if not isinstance(self.layout_certainty, (int, float)) or not math.isfinite(self.layout_certainty):
                raise ValueError(f"layout_certainty must be a finite real number, got {self.layout_certainty}")
            if not (0.0 <= float(self.layout_certainty) <= 1.0):
                raise ValueError(f"layout_certainty must be in [0.0, 1.0], got {self.layout_certainty}")

    def canonical_stratum(self) -> str:
        """Derive the canonical 5-part stratum key if not explicitly set."""
        if self.stratum:
            return self.stratum
        return stratum_key(
            field_class=self.field_class or "default",
            script=self.script or "default",
            print_or_handwriting=self.print_or_handwriting or "printed",
            legibility_band=self.legibility_band or "medium",
            writer_cluster_id=self.writer_cluster_id or "default",
        )


@dataclass(frozen=True)
class NoveltyResult:
    """Structured decision output of field- or page-level novelty detection (FR-CNF-14)."""

    novelty_score: float
    is_outside_regime: bool
    regime_reason: str
    cluster_id: str | None = None
    model_version: str = ""
    config_version: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.novelty_score):
            raise ValueError(f"novelty_score must be a finite real number, got {self.novelty_score}")
        if not (0.0 <= self.novelty_score <= 1.0):
            raise ValueError(f"novelty_score must be in [0.0, 1.0], got {self.novelty_score}")


class NoveltyDetector(Protocol):
    """Protocol for P0 novelty detector implementations."""

    def evaluate_novelty(
        self,
        features: NoveltyFeatures,
        policy: NoveltyPolicy | None = None,
    ) -> NoveltyResult:
        """Evaluate novelty features and return a structured NoveltyResult."""
        ...


@dataclass(frozen=True)
class DeterministicNoveltyModel:
    """Testable reference implementation of novelty model boundary."""

    evaluation_fn: Callable[[NoveltyFeatures], NoveltyResult]
    model_version: str = "deterministic_novelty_v1"
    config_version: str = ""

    def evaluate_novelty(
        self,
        features: NoveltyFeatures,
        policy: NoveltyPolicy | None = None,
    ) -> NoveltyResult:
        return self.evaluation_fn(features)


class CentroidDistanceNoveltyModel:
    """P0 reference novelty detector based on stratum prototype embedding centroid distance.

    ENGINEERING IMPLEMENTATION CHOICE:
    Computes normalized Euclidean distance from known calibrated stratum embedding prototypes.
    If the canonical stratum is unknown or uncalibrated, classifies the input as outside-regime
    with explainable reason 'unseen_stratum'.
    Strictly decoupled from calibration confidence formulas.
    """

    def __init__(
        self,
        stratum_prototypes: dict[str, list[float]] | None = None,
        policy: NoveltyPolicy | None = None,
        model_version: str = "novelty_v1.0.0",
        config_version: str = "",
    ) -> None:
        self.prototypes: dict[str, list[float]] = stratum_prototypes or {}
        self.policy: NoveltyPolicy = policy or NoveltyPolicy()
        self.model_version = model_version
        self.config_version = config_version

    def evaluate_novelty(
        self,
        features: NoveltyFeatures,
        policy: NoveltyPolicy | None = None,
    ) -> NoveltyResult:
        """Evaluate input representation distance against calibrated stratum prototypes."""
        active_policy = policy or self.policy
        stratum = features.canonical_stratum()

        # 1. Stratum familiarity check
        stratum_is_known = stratum in self.prototypes if self.prototypes else False

        if self.prototypes and not stratum_is_known:
            if active_policy.reject_unseen_strata:
                cluster_key = self._generate_cluster_id(features, stratum)
                return NoveltyResult(
                    novelty_score=1.0,
                    is_outside_regime=True,
                    regime_reason="unseen_stratum",
                    cluster_id=cluster_key,
                    model_version=self.model_version,
                    config_version=self.config_version,
                )

        # 2. Representation embedding distance evaluation
        if features.embedding is not None and stratum_is_known:
            prototype = self.prototypes[stratum]
            if len(features.embedding) != len(prototype):
                raise ValueError(
                    f"Embedding dimension mismatch: expected {len(prototype)}, got {len(features.embedding)}"
                )

            dim = len(prototype)
            dist_sq = sum((e - p) ** 2 for e, p in zip(features.embedding, prototype))
            normalized_dist = math.sqrt(dist_sq) / math.sqrt(dim)
            score = min(1.0, max(0.0, normalized_dist))

            is_outside = active_policy.is_outside_regime(score, stratum_is_known=True)
            reason = "embedding_distance_exceeded" if is_outside else "in_regime"
            cluster_key = self._generate_cluster_id(features, stratum) if is_outside else None

            return NoveltyResult(
                novelty_score=score,
                is_outside_regime=is_outside,
                regime_reason=reason,
                cluster_id=cluster_key,
                model_version=self.model_version,
                config_version=self.config_version,
            )

        # 3. Fallback when embedding is omitted (metadata / known stratum boundary)
        if stratum_is_known or not self.prototypes:
            return NoveltyResult(
                novelty_score=0.0,
                is_outside_regime=False,
                regime_reason="in_regime",
                cluster_id=None,
                model_version=self.model_version,
                config_version=self.config_version,
            )

        cluster_key = self._generate_cluster_id(features, stratum)
        return NoveltyResult(
            novelty_score=1.0,
            is_outside_regime=True,
            regime_reason="unseen_stratum",
            cluster_id=cluster_key,
            model_version=self.model_version,
            config_version=self.config_version,
        )

    def _generate_cluster_id(self, features: NoveltyFeatures, stratum: str) -> str:
        """Generate deterministic cluster handle for operational alert deduplication."""
        if features.writer_cluster_id and features.writer_cluster_id != "default":
            return f"novelty:writer:{features.writer_cluster_id}"
        if features.doc_type and features.script:
            return f"novelty:doc:{features.doc_type}:{features.script}"
        return f"novelty:stratum:{stratum}"


def score_novelty(
    embedding: list[float],
    stratum: str,
    prototypes: dict[str, list[float]] | None = None,
) -> float:
    """Functional convenience interface scoring embedding novelty against known stratum prototype."""
    if not isinstance(embedding, (list, tuple)) or len(embedding) == 0:
        raise ValueError("embedding must be a non-empty list of finite real numbers")
    for idx, v in enumerate(embedding):
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"embedding[{idx}] must be a finite real number, got {v}")

    if prototypes is None or stratum not in prototypes:
        return 1.0

    proto = prototypes[stratum]
    if len(embedding) != len(proto):
        raise ValueError(f"Dimension mismatch: expected {len(proto)}, got {len(embedding)}")

    dim = len(proto)
    dist_sq = sum((e - p) ** 2 for e, p in zip(embedding, proto))
    normalized_dist = math.sqrt(dist_sq) / math.sqrt(dim)
    return min(1.0, max(0.0, normalized_dist))


def validate_work_envelope_novelty(work_envelope: dict[str, Any]) -> tuple[str, str]:
    """Validate and extract pinned novelty_detector model version and config_version from WorkEnvelope."""
    if not isinstance(work_envelope, dict) or not work_envelope:
        raise ValueError("work_envelope must be a non-empty dictionary")

    models = work_envelope.get("model_versions")
    if not isinstance(models, dict) or "novelty_detector" not in models:
        raise ValueError("work_envelope missing pinned 'novelty_detector' model version")

    model_version = models["novelty_detector"]
    if not model_version or not isinstance(model_version, str):
        raise ValueError("work_envelope pinned 'novelty_detector' must be a non-empty string")

    config_version = work_envelope.get("config_version")
    if not config_version or not isinstance(config_version, str):
        raise ValueError("work_envelope missing pinned 'config_version'")

    return model_version, config_version
