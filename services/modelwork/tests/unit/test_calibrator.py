"""Unit tests for the field-level confidence calibrator and evaluation layer (FR-CNF-01, FR-CNF-02, FR-CNF-03)."""
from __future__ import annotations

import dataclasses
from typing import Any

try:
    import pytest
except ImportError:  # pragma: no cover
    class _MockPytest:
        @staticmethod
        def approx(val: float, rel: float | None = None, abs: float | None = None) -> Any:
            tol = abs if abs is not None else 1e-6

            class _Approx:
                def __init__(self, expected: float) -> None:
                    self.expected = expected

                def __eq__(self, actual: object) -> bool:
                    if isinstance(actual, (int, float)):
                        diff = self.expected - actual
                        return -tol <= diff <= tol
                    return False

            return _Approx(val)

        @staticmethod
        def raises(exc: type[BaseException]) -> Any:
            class _RaisesContext:
                def __enter__(self) -> _RaisesContext:
                    return self

                def __exit__(
                    self,
                    exc_type: type[BaseException] | None,
                    exc_val: BaseException | None,
                    exc_tb: object,
                ) -> bool:
                    return exc_type is not None and issubclass(exc_type, exc)

            return _RaisesContext()

    pytest = _MockPytest()  # type: ignore[assignment]

from modelwork.domain.calibration.calibrator import (
    DEFAULT_ECE_NUM_BINS,
    CalibrationRegime,
    CalibrationResult,
    CalibratorFeatureEncoder,
    CalibratorFeatures,
    CorrectionLabel,
    DeterministicCalibratorModel,
    ECEEvaluation,
    FeatureEncodingPolicy,
    LogisticRegressionCalibrator,
    PerStratumCalibrator,
    ThresholdPolicy,
    calibrate,
    compute_ece,
    evaluate_calibration,
    partition_training_and_evaluation,
    train_per_stratum_calibrators,
)
from modelwork.domain.learning_loop.guard import LeakageDetected
from modelwork.domain.stratum import stratum_key


def _make_sample_label(
    extraction_id: str,
    page_digest: str,
    is_correct: bool,
    token_conf: float = 0.85,
    field_class: str = "survey_number",
    script: str = "devanagari",
    print_or_hw: str = "handwritten",
    legibility: str = "medium",
    doc_type: str = "ror",
    writer_cluster: str = "cluster_01",
) -> CorrectionLabel:
    features = CalibratorFeatures(
        token_confidence=token_conf,
        layout_certainty=0.90,
        normalization_confidence=0.88,
        validator_outcomes={"syntax": "PASS", "range": "PASS"},
        field_class=field_class,
        script=script,
        doc_type=doc_type,
        print_or_handwriting=print_or_hw,
        legibility_band=legibility,
        writer_cluster_id=writer_cluster,
    )
    return CorrectionLabel(
        extraction_id=extraction_id,
        is_correct=is_correct,
        features=features,
        stratum=features.canonical_stratum(),
        source_page_digest=page_digest,
    )


# 1. learned model can be trained from valid labeled examples
def test_learned_model_trained_from_labeled_examples():
    """Learned calibrator fits weights from labeled examples and produces probabilities."""
    labels = [
        _make_sample_label(f"ext_{i}", f"page_{i % 3}", is_correct=(i % 2 == 0), token_conf=0.5 + (i * 0.04))
        for i in range(12)
    ]
    model = LogisticRegressionCalibrator(learning_rate=0.5, iterations=50)
    assert model.is_fitted is False

    model.fit_labels(labels)
    assert model.is_fitted is True
    assert len(model.weights) > 0

    test_feat = labels[0].features
    prob, attributions = model.predict_calibrated_confidence(test_feat)
    assert 0.0 <= prob <= 1.0
    assert isinstance(attributions, dict)


# 2. learned parameters change when training labels change
def test_learned_parameters_change_when_labels_change():
    """Different training labels produce distinct learned weights and posterior probabilities."""
    base_feats = [
        CalibratorFeatures(token_confidence=0.5 + i * 0.04, field_class="survey_number")
        for i in range(10)
    ]
    labels_high = [(feat, True) for feat in base_feats]
    labels_low = [(feat, False) for feat in base_feats]

    m_high = LogisticRegressionCalibrator(learning_rate=0.5, iterations=100).fit(labels_high)
    m_low = LogisticRegressionCalibrator(learning_rate=0.5, iterations=100).fit(labels_low)

    # Bias and probabilities should reflect positive vs negative ground truth
    assert m_high.bias > m_low.bias

    test_feat = base_feats[5]
    p_high, _ = m_high.predict_calibrated_confidence(test_feat)
    p_low, _ = m_low.predict_calibrated_confidence(test_feat)
    assert p_high > p_low


# 3. identical training data produces deterministic behavior
def test_identical_training_data_produces_deterministic_behavior():
    """Identical training data produces identical fitted parameters and predictions."""
    labels = [
        _make_sample_label(f"ext_{i}", f"page_{i % 2}", is_correct=(i % 3 != 0), token_conf=0.6 + (i * 0.03))
        for i in range(10)
    ]
    m1 = LogisticRegressionCalibrator(learning_rate=0.5, iterations=80).fit_labels(labels)
    m2 = LogisticRegressionCalibrator(learning_rate=0.5, iterations=80).fit_labels(labels)

    assert m1.weights == m2.weights
    assert m1.bias == m2.bias

    test_feat = labels[0].features
    p1, a1 = m1.predict_calibrated_confidence(test_feat)
    p2, a2 = m2.predict_calibrated_confidence(test_feat)
    assert p1 == p2
    assert a1 == a2


# 4. calibrated probability is bounded in [0,1]
def test_calibrated_probability_bounded():
    """Calibrated probability remains strictly bounded in [0.0, 1.0] across extreme inputs."""
    labels = [
        _make_sample_label("ext_1", "p1", True, token_conf=0.99),
        _make_sample_label("ext_2", "p2", False, token_conf=0.10),
    ]
    model = LogisticRegressionCalibrator().fit_labels(labels)

    extreme_high = CalibratorFeatures(token_confidence=10.0, layout_certainty=5.0)
    extreme_low = CalibratorFeatures(token_confidence=-5.0, layout_certainty=-5.0)

    p_high, _ = model.predict_calibrated_confidence(extreme_high)
    p_low, _ = model.predict_calibrated_confidence(extreme_low)

    assert 0.0 <= p_high <= 1.0
    assert 0.0 <= p_low <= 1.0


# 5. invalid/non-finite inputs rejected explicitly
def test_invalid_non_finite_inputs_rejected():
    """CalibratorFeatures rejects NaN and infinite float values."""
    with pytest.raises(ValueError):
        CalibratorFeatures(token_confidence=float("nan"))

    with pytest.raises(ValueError):
        CalibratorFeatures(token_confidence=0.8, layout_certainty=float("inf"))

    with pytest.raises(ValueError):
        CalibratorFeatures(token_confidence=0.8, normalization_confidence=float("-inf"))


# 6. raw confidence remains distinct semantically from calibrated confidence
def test_raw_confidence_distinct_from_calibrated():
    """CalibrationResult structurally preserves raw confidence distinct from calibrated confidence."""
    feat = CalibratorFeatures(token_confidence=0.88, field_class="survey_number")
    res = calibrate(feat)

    assert res.raw_confidence == 0.88
    assert res.calibrated_confidence != res.raw_confidence
    assert 0.0 <= res.calibrated_confidence <= 1.0


# 7. categorical/numerical feature encoding is deterministic
def test_feature_encoding_deterministic():
    """CalibratorFeatureEncoder produces deterministic feature names and vectors."""
    feat1 = CalibratorFeatures(
        token_confidence=0.85,
        layout_certainty=0.92,
        normalization_confidence=0.80,
        validator_outcomes={"checksum": "PASS", "range": "FAIL"},
        field_class="parcel_id",
        script="kannada",
        doc_type="mutation",
        print_or_handwriting="handwritten",
        legibility_band="poor",
    )
    encoder = CalibratorFeatureEncoder.from_features_list([feat1])
    vec1 = encoder.encode(feat1)
    vec2 = encoder.encode(feat1)

    assert vec1 == vec2
    assert len(vec1) == len(encoder.feature_names)


# 8. configurable feature encoding policy and missingness indicators
def test_configurable_feature_encoding_policy():
    """FeatureEncodingPolicy configures default imputation values and missingness flags."""
    custom_policy = FeatureEncodingPolicy(
        default_layout_certainty=0.75,
        default_normalization_confidence=0.65,
        default_empty_validator_pass_ratio=0.80,
        include_missingness_indicators=True,
    )
    encoder = CalibratorFeatureEncoder(policy=custom_policy)

    feat_missing = CalibratorFeatures(token_confidence=0.9)  # layout and norm are None
    vec = encoder.encode(feat_missing)

    names = encoder.feature_names
    assert "layout_is_missing" in names
    assert "normalization_is_missing" in names
    assert "has_validators" in names

    layout_idx = names.index("layout_certainty")
    layout_missing_idx = names.index("layout_is_missing")
    assert vec[layout_idx] == 0.75
    assert vec[layout_missing_idx] == 1.0


# 9. canonical 5-part stratum is preserved
def test_canonical_5_part_stratum_preserved():
    """Canonical stratum computation strictly invokes domain.stratum.stratum_key."""
    feat = CalibratorFeatures(
        token_confidence=0.8,
        field_class="survey_number",
        script="devanagari",
        print_or_handwriting="handwritten",
        legibility_band="poor",
        writer_cluster_id="cluster_02",
    )
    expected = stratum_key(
        field_class="survey_number",
        script="devanagari",
        print_or_handwriting="handwritten",
        legibility_band="poor",
        writer_cluster_id="cluster_02",
    )
    assert feat.canonical_stratum() == expected
    assert feat.canonical_stratum() == "survey_number|devanagari|handwritten|poor|cluster_02"


# 10. writer cluster not accidentally injected into pooled model features
def test_writer_cluster_not_in_pooled_model_features():
    """writer_cluster_id is strictly stratum identity and must not be a pooled model feature."""
    feat = CalibratorFeatures(
        token_confidence=0.8,
        field_class="survey_number",
        script="devanagari",
        writer_cluster_id="cluster_memorize_me",
    )
    encoder = CalibratorFeatureEncoder.from_features_list([feat])

    assert all("cluster_memorize_me" not in name for name in encoder.feature_names)
    assert all("writer_cluster" not in name for name in encoder.feature_names)


# 11. stratum-specific model is selected when evidence is sufficient
def test_stratum_specific_selected_when_evidence_sufficient():
    """PerStratumCalibrator routes to stratum model when stratum samples >= min_stratum_samples."""
    target_stratum = "survey_number|devanagari|handwritten|medium|cluster_01"
    labels_target = [
        _make_sample_label(f"ext_t_{i}", f"p_{i}", is_correct=True)
        for i in range(6)
    ]
    labels_other = [
        _make_sample_label(f"ext_o_{i}", f"po_{i}", is_correct=False, field_class="owner_name")
        for i in range(6)
    ]
    calibrator = train_per_stratum_calibrators(
        labels_target + labels_other,
        min_stratum_samples=5,
    )

    test_feat = labels_target[0].features
    res = calibrator.calibrate(test_feat)

    assert res.regime == CalibrationRegime.STRATUM_SPECIFIC
    assert res.stratum == target_stratum


# 12. pooled fallback is selected when evidence is insufficient
def test_pooled_fallback_selected_when_evidence_insufficient():
    """PerStratumCalibrator routes to pooled fallback when stratum evidence is insufficient."""
    labels_target = [
        _make_sample_label(f"ext_t_{i}", f"p_{i}", is_correct=True)
        for i in range(3)
    ]
    labels_other = [
        _make_sample_label(f"ext_o_{i}", f"po_{i}", is_correct=False, field_class="owner_name")
        for i in range(10)
    ]
    # min_stratum_samples is 5; target has only 3 samples
    calibrator = train_per_stratum_calibrators(
        labels_target + labels_other,
        min_stratum_samples=5,
    )

    test_feat = labels_target[0].features
    res = calibrator.calibrate(test_feat)

    assert res.regime == CalibrationRegime.POOLED_FALLBACK


# 13. unseen stratum falls back to pooled model
def test_unseen_stratum_falls_back_to_pooled():
    """Unseen stratum without training presence routes cleanly to pooled fallback."""
    labels = [_make_sample_label(f"ext_{i}", f"p_{i}", is_correct=True) for i in range(10)]
    calibrator = train_per_stratum_calibrators(labels, min_stratum_samples=5)

    unseen_feat = CalibratorFeatures(
        token_confidence=0.8,
        field_class="unseen_field",
        script="bengali",
        print_or_handwriting="printed",
        legibility_band="good",
        writer_cluster_id="unseen_cluster",
    )
    res = calibrator.calibrate(unseen_feat)

    assert res.regime == CalibrationRegime.POOLED_FALLBACK


# 14. calibration regime is explicitly recorded
def test_calibration_regime_explicitly_recorded():
    """CalibrationResult explicitly records either STRATUM_SPECIFIC or POOLED_FALLBACK."""
    pooled = DeterministicCalibratorModel(lambda f: (0.75, {}))
    stratum_m = DeterministicCalibratorModel(lambda f: (0.90, {}))
    s_key = "field|script|handwritten|good|"

    calibrator = PerStratumCalibrator(
        pooled_model=pooled,
        stratum_models={s_key: stratum_m},
        stratum_sample_counts={s_key: 10},
        min_stratum_samples=5,
    )

    feat_stratum = CalibratorFeatures(token_confidence=0.8, field_class="field", script="script", print_or_handwriting="handwritten", legibility_band="good")
    res1 = calibrator.calibrate(feat_stratum)
    assert res1.regime == CalibrationRegime.STRATUM_SPECIFIC

    feat_pooled = CalibratorFeatures(token_confidence=0.8, field_class="other", script="script", print_or_handwriting="printed", legibility_band="good")
    res2 = calibrator.calibrate(feat_pooled)
    assert res2.regime == CalibrationRegime.POOLED_FALLBACK


# 15. no novelty feature enters the learned feature vector
def test_no_novelty_in_learned_feature_vector():
    """Novelty is strictly decoupled from calibrator features and encoder feature names (FR-CNF-14)."""
    feature_fields = [f.name for f in dataclasses.fields(CalibratorFeatures)]
    assert "novelty" not in feature_fields
    assert "novelty_score" not in feature_fields

    encoder = CalibratorFeatureEncoder(extra_categories=["field_class=survey", "script=devanagari"])
    assert all("novelty" not in name for name in encoder.feature_names)


# 16. threshold derives from configured target error rate
def test_threshold_policy_derives_from_target_error_rate():
    """ThresholdPolicy derives required confidence directly as (1.0 - target_error_rate)."""
    policy_1pct = ThresholdPolicy(target_error_rate=0.01)
    assert policy_1pct.required_confidence == pytest.approx(0.99)
    assert policy_1pct.is_auto_acceptable(0.992) is True
    assert policy_1pct.is_auto_acceptable(0.988) is False

    policy_5pct = ThresholdPolicy(target_error_rate=0.05)
    assert policy_5pct.required_confidence == pytest.approx(0.95)
    assert policy_5pct.is_auto_acceptable(0.95) is True
    assert policy_5pct.is_auto_acceptable(0.949) is False


# 17. invalid target error configuration is rejected
def test_invalid_target_error_configuration_rejected():
    """ThresholdPolicy rejects boundary and out-of-range target error rates."""
    with pytest.raises(ValueError):
        ThresholdPolicy(target_error_rate=0.0)

    with pytest.raises(ValueError):
        ThresholdPolicy(target_error_rate=1.0)

    with pytest.raises(ValueError):
        ThresholdPolicy(target_error_rate=-0.05)

    with pytest.raises(ValueError):
        ThresholdPolicy(target_error_rate=1.5)


# 18. ECE is computed from predictions and correctness labels
def test_ece_computed_from_predictions_and_labels():
    """compute_ece calculates mathematically accurate Expected Calibration Error over binned predictions."""
    preds = [0.85, 0.85, 0.85, 0.85]
    labels = [True, True, True, False]
    ece = compute_ece(preds, labels, num_bins=10)
    assert ece == pytest.approx(0.10)


# 19. configurable ECE bin count
def test_configurable_ece_num_bins():
    """compute_ece accepts configurable bin counts (FR-CNF-02, FR-LRN-08)."""
    preds = [0.1, 0.2, 0.8, 0.9]
    labels = [False, False, True, True]

    ece_5 = compute_ece(preds, labels, num_bins=5)
    ece_20 = compute_ece(preds, labels, num_bins=20)

    assert 0.0 <= ece_5 <= 1.0
    assert 0.0 <= ece_20 <= 1.0
    assert DEFAULT_ECE_NUM_BINS == 10


# 20. ECE is evaluated against configurable maximum ECE
def test_ece_evaluated_against_configurable_max():
    """evaluate_calibration compares observed ECE against configurable threshold."""
    preds = [0.9, 0.9, 0.9, 0.9]
    labels = [True, True, True, False]  # Acc = 0.75, Conf = 0.9, ECE = 0.15

    eval_strict = evaluate_calibration(
        preds, labels, stratum="s1", regime=CalibrationRegime.STRATUM_SPECIFIC, max_acceptable_ece=0.05
    )
    assert eval_strict.is_acceptable is False

    eval_lenient = evaluate_calibration(
        preds, labels, stratum="s1", regime=CalibrationRegime.STRATUM_SPECIFIC, max_acceptable_ece=0.20
    )
    assert eval_lenient.is_acceptable is True


# 21. ECE preserves stratum/regime metadata
def test_ece_preserves_stratum_and_regime_metadata():
    """ECEEvaluation preserves stratum, sample count, and calibration regime metadata."""
    eval_res = evaluate_calibration(
        predictions=[0.9, 0.8],
        labels=[True, True],
        stratum="survey_number|devanagari|handwritten|medium|c1",
        regime=CalibrationRegime.STRATUM_SPECIFIC,
        max_acceptable_ece=0.05,
        num_bins=5,
    )
    assert eval_res.stratum == "survey_number|devanagari|handwritten|medium|c1"
    assert eval_res.regime == CalibrationRegime.STRATUM_SPECIFIC
    assert eval_res.sample_count == 2
    assert eval_res.max_acceptable_ece == 0.05


# 22. page provenance boundary supported by actual identifiers
def test_page_provenance_isolation_boundary():
    """partition_training_and_evaluation enforces page-level isolation via source_page_digest."""
    labels = [
        _make_sample_label("ext_1", "doc_page_A", True),
        _make_sample_label("ext_2", "doc_page_A", False),
        _make_sample_label("ext_3", "doc_page_B", True),
        _make_sample_label("ext_4", "doc_page_B", True),
        _make_sample_label("ext_5", "doc_page_C", False),
        _make_sample_label("ext_6", "doc_page_C", True),
    ]
    train_split, eval_split = partition_training_and_evaluation(labels, eval_fraction=0.34)

    train_pages = {lbl.source_page_digest for lbl in train_split}
    eval_pages = {lbl.source_page_digest for lbl in eval_split}

    # Strict zero page-level leakage
    assert len(train_pages & eval_pages) == 0
    assert len(train_split) + len(eval_split) == len(labels)


# 23. regression suite exclusion list enforcement
def test_regression_suite_exclusion():
    """Overlapping regression suite exclusion list triggers LeakageDetected."""
    labels = [
        _make_sample_label("ext_1", "doc_page_A", True),
        _make_sample_label("ext_2", "doc_page_B", False),
    ]
    with pytest.raises(LeakageDetected):
        partition_training_and_evaluation(labels, regression_suite_exclusion={"doc_page_A"})


# 24. feature attributions are not fabricated when unsupported
def test_feature_attributions_not_fabricated_when_unsupported():
    """Unfitted model returns empty feature attributions rather than arbitrary fabricated numbers."""
    unfitted_model = LogisticRegressionCalibrator()
    feat = CalibratorFeatures(token_confidence=0.8)
    prob, attributions = unfitted_model.predict_calibrated_confidence(feat)

    assert attributions == {}
    assert prob == 0.8


if __name__ == "__main__":
    test_learned_model_trained_from_labeled_examples()
    test_learned_parameters_change_when_labels_change()
    test_identical_training_data_produces_deterministic_behavior()
    test_calibrated_probability_bounded()
    test_invalid_non_finite_inputs_rejected()
    test_raw_confidence_distinct_from_calibrated()
    test_feature_encoding_deterministic()
    test_configurable_feature_encoding_policy()
    test_canonical_5_part_stratum_preserved()
    test_writer_cluster_not_in_pooled_model_features()
    test_stratum_specific_selected_when_evidence_sufficient()
    test_pooled_fallback_selected_when_evidence_insufficient()
    test_unseen_stratum_falls_back_to_pooled()
    test_calibration_regime_explicitly_recorded()
    test_no_novelty_in_learned_feature_vector()
    test_threshold_policy_derives_from_target_error_rate()
    test_invalid_target_error_configuration_rejected()
    test_ece_computed_from_predictions_and_labels()
    test_configurable_ece_num_bins()
    test_ece_evaluated_against_configurable_max()
    test_ece_preserves_stratum_and_regime_metadata()
    test_page_provenance_isolation_boundary()
    test_regression_suite_exclusion()
    test_feature_attributions_not_fabricated_when_unsupported()
    print("All 24 calibrator unit tests passed successfully.")
