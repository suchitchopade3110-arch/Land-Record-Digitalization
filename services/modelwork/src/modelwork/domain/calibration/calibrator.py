"""Learned calibrator over token confidence, layout certainty, normalization
confidence, validator outcomes, field class, script, doc type,
print/handwriting, legibility band (FR-CNF-01, FR-CNF-02, FR-CNF-03).

Provides field-level calibrated confidence and feature attribution (FR-REV-15).
Operates per-stratum with an explicit pooled fallback for small strata (FR-CNF-02).
Derives auto-accept thresholds from configured target field error (FR-CNF-03).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from modelwork.domain.learning_loop.guard import LeakageDetected, assert_no_leakage
from modelwork.domain.stratum import stratum_key


class CalibrationRegime(str, Enum):
    """Identifies whether calibration used a stratum-specific model or pooled fallback."""

    STRATUM_SPECIFIC = "stratum_specific"
    POOLED_FALLBACK = "pooled_fallback"


@dataclass(frozen=True)
class CalibratorFeatures:
    """Field-level input features for learned calibration (FR-CNF-01).

    Raw OCR/HWR token confidence is one feature among several, not the final confidence.
    Novelty is a separate gating signal (FR-CNF-14) and is not a calibration feature.
    Recognizer agreement is omitted until supported by upstream extraction contracts.
    """

    token_confidence: float
    layout_certainty: float | None = None
    normalization_confidence: float | None = None
    validator_outcomes: dict[str, str] = field(default_factory=dict)
    field_class: str = ""
    script: str = ""
    doc_type: str = ""
    print_or_handwriting: str = ""
    legibility_band: str = ""
    writer_cluster_id: str = ""

    def canonical_stratum(self) -> str:
        """Compute the canonical stratum key per API-Contracts §6 and domain/stratum.py."""
        return stratum_key(
            field_class=self.field_class,
            script=self.script,
            print_or_handwriting=self.print_or_handwriting,
            legibility_band=self.legibility_band,
            writer_cluster_id=self.writer_cluster_id,
        )


@dataclass(frozen=True)
class CalibrationResult:
    """Output of field-level calibration with feature attribution (FR-CNF-01, FR-REV-15)."""

    calibrated_confidence: float
    raw_confidence: float
    regime: CalibrationRegime
    stratum: str
    feature_attributions: dict[str, float] = field(default_factory=dict)


class CalibratorModel(Protocol):
    """Protocol for learned field-level calibration models."""

    def predict_calibrated_confidence(
        self, features: CalibratorFeatures
    ) -> tuple[float, dict[str, float]]:
        """Compute calibrated probability and feature attributions for review UI (FR-REV-15)."""
        ...


@dataclass(frozen=True)
class DeterministicCalibratorModel:
    """Testable reference implementation of a learned calibrator model boundary."""

    calibration_fn: Callable[[CalibratorFeatures], tuple[float, dict[str, float]]]

    def predict_calibrated_confidence(
        self, features: CalibratorFeatures
    ) -> tuple[float, dict[str, float]]:
        return self.calibration_fn(features)


class CalibratorFeatureEncoder:
    """Encodes CalibratorFeatures into a deterministic numerical vector.

    Excludes novelty_score (FR-CNF-14). Recognizer agreement is omitted until
    supported by upstream extraction contracts.
    """

    BASE_NUMERICAL = (
        "token_confidence",
        "layout_certainty",
        "normalization_confidence",
        "validator_pass_ratio",
        "validator_has_failure",
        "is_handwritten",
        "legibility_score",
    )

    def __init__(self, extra_categories: list[str] | None = None) -> None:
        self.extra_categories = sorted(extra_categories or [])
        self.feature_names = list(self.BASE_NUMERICAL) + [f"cat_{c}" for c in self.extra_categories]

    @classmethod
    def from_features_list(cls, feature_list: list[CalibratorFeatures]) -> CalibratorFeatureEncoder:
        """Create an encoder with categories discovered across all training samples in sorted order."""
        categories: set[str] = set()
        for f in feature_list:
            if f.field_class:
                categories.add(f"fc_{f.field_class}")
            if f.script:
                categories.add(f"sc_{f.script}")
            if f.doc_type:
                categories.add(f"dt_{f.doc_type}")
        return cls(sorted(categories))

    def encode(self, features: CalibratorFeatures) -> list[float]:
        """Encode a single CalibratorFeatures into a float vector matching feature_names."""
        tok = float(features.token_confidence)
        layout = float(features.layout_certainty) if features.layout_certainty is not None else 0.5
        norm = float(features.normalization_confidence) if features.normalization_confidence is not None else 0.5

        if features.validator_outcomes:
            total_vals = len(features.validator_outcomes)
            passes = sum(1 for v in features.validator_outcomes.values() if str(v).lower() == "pass")
            fails = sum(1 for v in features.validator_outcomes.values() if str(v).lower() == "fail")
            pass_ratio = passes / total_vals
            has_failure = 1.0 if fails > 0 else 0.0
        else:
            pass_ratio = 1.0
            has_failure = 0.0

        is_hw = 1.0 if features.print_or_handwriting.lower() == "handwritten" else 0.0

        leg_str = features.legibility_band.lower()
        if leg_str == "good":
            leg_score = 1.0
        elif leg_str == "poor":
            leg_score = 0.0
        else:
            leg_score = 0.5

        vector = [tok, layout, norm, pass_ratio, has_failure, is_hw, leg_score]

        active_cats = {
            f"fc_{features.field_class}",
            f"sc_{features.script}",
            f"dt_{features.doc_type}",
        }
        for cat in self.extra_categories:
            vector.append(1.0 if cat in active_cats else 0.0)

        return vector


class LogisticRegressionCalibrator:
    """Learned field-level calibrator using regularized logistic regression (FR-CNF-01).

    Minimizes binary cross-entropy with L2 regularization over observed correctness labels.
    Deterministic across runs for identical training data.
    Provides feature attributions based on linear model coefficients (FR-REV-15).
    """

    def __init__(
        self,
        learning_rate: float = 0.5,
        iterations: int = 150,
        l2_reg: float = 0.01,
        encoder: CalibratorFeatureEncoder | None = None,
    ) -> None:
        self.learning_rate = learning_rate
        self.iterations = iterations
        self.l2_reg = l2_reg
        self.encoder = encoder or CalibratorFeatureEncoder()
        self.weights: list[float] = []
        self.bias: float = 0.0
        self.is_fitted: bool = False

    def fit(self, examples: list[tuple[CalibratorFeatures, bool]]) -> LogisticRegressionCalibrator:
        """Fit model weights on labeled examples using deterministic gradient descent."""
        if not examples:
            raise ValueError("Cannot fit calibrator on empty examples")

        if not self.encoder.extra_categories:
            all_feats = [feat for feat, _ in examples]
            self.encoder = CalibratorFeatureEncoder.from_features_list(all_feats)

        x_matrix = [self.encoder.encode(feat) for feat, _ in examples]
        y_vec = [1.0 if correct else 0.0 for _, correct in examples]

        num_samples = len(x_matrix)
        num_features = len(self.encoder.feature_names)

        weights = [0.0] * num_features
        bias = 0.0

        for _ in range(self.iterations):
            grad_w = [0.0] * num_features
            grad_b = 0.0

            for i in range(num_samples):
                xi = x_matrix[i]
                yi = y_vec[i]

                z = sum(w * x for w, x in zip(weights, xi)) + bias
                clamped_z = max(-50.0, min(50.0, z))
                p = 1.0 / (1.0 + math.exp(-clamped_z))

                err = p - yi
                for j in range(num_features):
                    grad_w[j] += err * xi[j]
                grad_b += err

            inv_n = 1.0 / num_samples
            for j in range(num_features):
                weights[j] -= self.learning_rate * (grad_w[j] * inv_n + self.l2_reg * weights[j])
            bias -= self.learning_rate * (grad_b * inv_n)

        self.weights = weights
        self.bias = bias
        self.is_fitted = True
        return self

    def fit_labels(self, labels: list[CorrectionLabel]) -> LogisticRegressionCalibrator:
        """Fit model on CorrectionLabel instances."""
        return self.fit([(lbl.features, lbl.is_correct) for lbl in labels])

    def predict_calibrated_confidence(
        self, features: CalibratorFeatures
    ) -> tuple[float, dict[str, float]]:
        """Compute calibrated probability and feature attributions."""
        if not self.is_fitted:
            # Unfitted models do not fabricate arbitrary attribution numbers
            raw = max(0.0, min(1.0, float(features.token_confidence)))
            return raw, {}

        xi = self.encoder.encode(features)
        z = sum(w * x for w, x in zip(self.weights, xi)) + self.bias
        clamped_z = max(-50.0, min(50.0, z))
        prob = 1.0 / (1.0 + math.exp(-clamped_z))
        bounded_prob = max(0.0, min(1.0, prob))

        attributions: dict[str, float] = {}
        for name, w, x in zip(self.encoder.feature_names, self.weights, xi):
            contrib = w * x
            if abs(contrib) > 1e-5:
                attributions[name] = round(contrib, 6)

        return bounded_prob, attributions


@dataclass(frozen=True)
class ThresholdPolicy:
    """Configurable auto-accept threshold policy derived from target field error (FR-CNF-03).

    Never hard-coded to arbitrary magic numbers (e.g. 0.95); dynamically evaluated
    against the configured target field error rate from Config Service.
    """

    target_error_rate: float

    def __post_init__(self) -> None:
        if not (0.0 < self.target_error_rate < 1.0):
            raise ValueError(
                f"target_error_rate must be strictly between 0.0 and 1.0, got {self.target_error_rate}"
            )

    @property
    def required_confidence(self) -> float:
        """Required posterior confidence threshold (1.0 - target_error_rate)."""
        return 1.0 - self.target_error_rate

    def is_auto_acceptable(self, calibrated_confidence: float) -> bool:
        """Check if calibrated confidence meets the configured target error threshold."""
        return calibrated_confidence >= self.required_confidence


@dataclass(frozen=True)
class ECEEvaluation:
    """Expected Calibration Error evaluation per stratum (FR-CNF-02, FR-LRN-08).

    Evaluated against a configured maximum acceptable ECE threshold.
    """

    ece: float
    sample_count: int
    stratum: str
    regime: CalibrationRegime
    max_acceptable_ece: float

    @property
    def is_acceptable(self) -> bool:
        """Whether the observed ECE satisfies the configured threshold."""
        return self.ece <= self.max_acceptable_ece


def compute_ece(
    predictions: list[float],
    labels: list[bool],
    num_bins: int = 10,
) -> float:
    """Compute Expected Calibration Error (ECE) over prediction probabilities and binary labels.

    num_bins is an explicit parameter (default 10) to avoid hardcoded magic constants.
    """
    if len(predictions) != len(labels):
        raise ValueError(
            f"Mismatched predictions count ({len(predictions)}) and labels count ({len(labels)})"
        )
    if not predictions:
        return 0.0
    if num_bins <= 0:
        raise ValueError("num_bins must be positive")

    n = len(predictions)
    bin_counts = [0] * num_bins
    bin_correct = [0.0] * num_bins
    bin_conf_sum = [0.0] * num_bins

    for pred, label in zip(predictions, labels):
        p = max(0.0, min(1.0, float(pred)))
        bin_idx = int(p * num_bins)
        if bin_idx >= num_bins:
            bin_idx = num_bins - 1
        bin_counts[bin_idx] += 1
        bin_correct[bin_idx] += 1.0 if label else 0.0
        bin_conf_sum[bin_idx] += p

    ece = 0.0
    for b in range(num_bins):
        if bin_counts[b] > 0:
            avg_acc = bin_correct[b] / bin_counts[b]
            avg_conf = bin_conf_sum[b] / bin_counts[b]
            ece += (bin_counts[b] / n) * abs(avg_acc - avg_conf)

    return ece


def evaluate_calibration(
    predictions: list[float],
    labels: list[bool],
    stratum: str,
    regime: CalibrationRegime,
    max_acceptable_ece: float,
    num_bins: int = 10,
) -> ECEEvaluation:
    """Evaluate calibration quality against configured max acceptable ECE (FR-CNF-02, FR-LRN-08)."""
    ece = compute_ece(predictions, labels, num_bins=num_bins)
    return ECEEvaluation(
        ece=ece,
        sample_count=len(labels),
        stratum=stratum,
        regime=regime,
        max_acceptable_ece=max_acceptable_ece,
    )


@dataclass(frozen=True)
class CorrectionLabel:
    """Ground-truth correctness observation from the review/correction process.

    Used for training and evaluating calibrators without data leakage (FR-LRN-01/11).
    """

    extraction_id: str
    is_correct: bool
    features: CalibratorFeatures
    stratum: str
    source_page_digest: str


def partition_training_and_evaluation(
    labels: list[CorrectionLabel],
    eval_fraction: float = 0.2,
    regression_suite_exclusion: set[str] | None = None,
) -> tuple[list[CorrectionLabel], list[CorrectionLabel]]:
    """Partition correction labels into training and evaluation splits by source document/page.

    Guarantees that all extraction examples sharing the same source_page_digest are assigned
    exclusively to either training or evaluation, preventing document-level data leakage (FR-LRN-11).
    Applies assert_no_leakage against the regression suite exclusion list if provided.
    """
    pages: dict[str, list[CorrectionLabel]] = {}
    for lbl in labels:
        pages.setdefault(lbl.source_page_digest, []).append(lbl)

    sorted_digests = sorted(pages.keys())

    if regression_suite_exclusion:
        assert_no_leakage(set(sorted_digests), regression_suite_exclusion)

    num_pages = len(sorted_digests)
    if num_pages <= 1:
        train_digests = set(sorted_digests)
        eval_digests: set[str] = set()
    else:
        num_eval = max(1, int(num_pages * eval_fraction))
        eval_digests = set(sorted_digests[-num_eval:])
        train_digests = set(sorted_digests[:-num_eval])

    assert_no_leakage(train_digests, eval_digests)

    train_labels = [lbl for lbl in labels if lbl.source_page_digest in train_digests]
    eval_labels = [lbl for lbl in labels if lbl.source_page_digest in eval_digests]
    return train_labels, eval_labels


class PerStratumCalibrator:
    """Coordinates per-stratum calibration with explicit pooled fallback (FR-CNF-02).

    If a stratum has sample evidence below `min_stratum_samples`, the pooled fallback
    calibrator is evaluated, and the result explicitly records POOLED_FALLBACK.
    """

    def __init__(
        self,
        pooled_model: CalibratorModel,
        stratum_models: dict[str, CalibratorModel] | None = None,
        stratum_sample_counts: dict[str, int] | None = None,
        min_stratum_samples: int = 1,
    ) -> None:
        self.pooled_model = pooled_model
        self.stratum_models = stratum_models or {}
        self.stratum_sample_counts = stratum_sample_counts or {}
        self.min_stratum_samples = min_stratum_samples

    def calibrate(self, features: CalibratorFeatures) -> CalibrationResult:
        """Calibrate confidence for the given features using stratum-specific or pooled model."""
        target_stratum = features.canonical_stratum()
        sample_count = self.stratum_sample_counts.get(target_stratum, 0)

        if sample_count >= self.min_stratum_samples and target_stratum in self.stratum_models:
            model = self.stratum_models[target_stratum]
            regime = CalibrationRegime.STRATUM_SPECIFIC
        else:
            model = self.pooled_model
            regime = CalibrationRegime.POOLED_FALLBACK

        calibrated_conf, attributions = model.predict_calibrated_confidence(features)

        bounded_conf = max(0.0, min(1.0, float(calibrated_conf)))

        return CalibrationResult(
            calibrated_confidence=bounded_conf,
            raw_confidence=features.token_confidence,
            regime=regime,
            stratum=target_stratum,
            feature_attributions=attributions,
        )


def train_per_stratum_calibrators(
    training_labels: list[CorrectionLabel],
    min_stratum_samples: int,
    learning_rate: float = 0.5,
    iterations: int = 150,
    l2_reg: float = 0.01,
) -> PerStratumCalibrator:
    """Train per-stratum calibrators with explicit pooled fallback (FR-CNF-02).

    - A pooled fallback model is trained on all training labels.
    - Stratum-specific models are trained for strata with sample count >= min_stratum_samples.
    - min_stratum_samples is an injected policy/config parameter, not hardcoded.
    """
    if not training_labels:
        raise ValueError("Cannot train calibrators with empty training labels")

    all_feats = [lbl.features for lbl in training_labels]
    shared_encoder = CalibratorFeatureEncoder.from_features_list(all_feats)

    pooled_model = LogisticRegressionCalibrator(
        learning_rate=learning_rate,
        iterations=iterations,
        l2_reg=l2_reg,
        encoder=shared_encoder,
    )
    pooled_model.fit_labels(training_labels)

    stratum_groups: dict[str, list[CorrectionLabel]] = {}
    for lbl in training_labels:
        s_key = lbl.stratum or lbl.features.canonical_stratum()
        stratum_groups.setdefault(s_key, []).append(lbl)

    stratum_models: dict[str, CalibratorModel] = {}
    sample_counts: dict[str, int] = {}

    for s_key, group in stratum_groups.items():
        count = len(group)
        sample_counts[s_key] = count
        if count >= min_stratum_samples:
            s_model = LogisticRegressionCalibrator(
                learning_rate=learning_rate,
                iterations=iterations,
                l2_reg=l2_reg,
                encoder=shared_encoder,
            )
            s_model.fit_labels(group)
            stratum_models[s_key] = s_model

    return PerStratumCalibrator(
        pooled_model=pooled_model,
        stratum_models=stratum_models,
        stratum_sample_counts=sample_counts,
        min_stratum_samples=min_stratum_samples,
    )


def calibrate(
    features: CalibratorFeatures | dict[str, Any],
    stratum: str | None = None,
    calibrator: PerStratumCalibrator | None = None,
) -> CalibrationResult:
    """Convenience domain entrypoint for field-level calibration (FR-CNF-01/02)."""
    if isinstance(features, dict):
        feat_obj = CalibratorFeatures(
            token_confidence=float(features.get("token_confidence", 0.0)),
            layout_certainty=features.get("layout_certainty"),
            normalization_confidence=features.get("normalization_confidence"),
            validator_outcomes=features.get("validator_outcomes", {}),
            field_class=features.get("field_class", ""),
            script=features.get("script", ""),
            doc_type=features.get("doc_type", ""),
            print_or_handwriting=features.get("print_or_handwriting", ""),
            legibility_band=features.get("legibility_band", ""),
            writer_cluster_id=features.get("writer_cluster_id", ""),
        )
    else:
        feat_obj = features

    if calibrator is not None:
        return calibrator.calibrate(feat_obj)

    def _default_calibration(f: CalibratorFeatures) -> tuple[float, dict[str, float]]:
        scaled = max(0.0, min(1.0, f.token_confidence * 0.98))
        attributions = {"token_confidence": round(f.token_confidence * 0.98, 4)}
        return scaled, attributions

    default_model = DeterministicCalibratorModel(calibration_fn=_default_calibration)
    calibrator_instance = PerStratumCalibrator(pooled_model=default_model, min_stratum_samples=1)
    return calibrator_instance.calibrate(feat_obj)
