"""Unit tests for the Field-Level Calibrator to Decision Flow Integration (FR-CNF-01..04, FR-CNF-15)."""
from __future__ import annotations

import json
import math
from pathlib import Path
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
    CalibrationRegime,
    CalibratorFeatures,
    CorrectionLabel,
    DeterministicCalibratorModel,
    ThresholdPolicy,
    train_per_stratum_calibrators,
)
from modelwork.domain.calibration.cold_start import ColdStartState
from modelwork.domain.calibration.decision_flow import (
    AuditSamplingPolicy,
    determine_routing_outcome,
    extraction_to_calibrator_features,
    process_extraction_for_decision,
)
from modelwork.domain.stratum import stratum_key


def _sample_work_envelope(
    calibrator_version: str = "calibrator_v1",
    config_version: str = "cfg_v2026_01",
    page_id: str = "11111111-1111-1111-1111-111111111111",
    document_id: str = "00000000-0000-0000-0000-000000000000",
) -> dict[str, Any]:
    return {
        "envelope_id": "env-123",
        "document_id": document_id,
        "page_id": page_id,
        "pinned_at": "2026-09-10T12:00:00Z",
        "model_versions": {
            "triage_classifier": "triage_v1",
            "printed_ocr": "ocr_v1",
            "hwr": "hwr_v1",
            "confidence_calibrator": calibrator_version,
            "novelty_detector": "novelty_v1",
        },
        "config_version": config_version,
    }


def _sample_extraction(
    extraction_id: str = "ext-456",
    field_name: str = "survey_number",
    token_conf: float = 0.92,
    engine: str = "printed_ocr",
    page_id: str = "11111111-1111-1111-1111-111111111111",
) -> dict[str, Any]:
    return {
        "id": extraction_id,
        "page_id": page_id,
        "field_name": field_name,
        "raw_value": "42/B",
        "engine": engine,
        "token_confidence": token_conf,
        "entry_status": "unknown",
    }


def _sample_trained_calibrator():
    target_stratum = "survey_number|devanagari|printed|good|cluster_01"
    labels = []
    for i in range(8):
        feat = CalibratorFeatures(
            token_confidence=0.70 + i * 0.03,
            field_class="survey_number",
            script="devanagari",
            print_or_handwriting="printed",
            legibility_band="good",
            writer_cluster_id="cluster_01",
        )
        labels.append(
            CorrectionLabel(
                extraction_id=f"ext_{i}",
                is_correct=True,
                features=feat,
                stratum=target_stratum,
                source_page_digest=f"digest_{i}",
            )
        )
    return train_per_stratum_calibrators(labels, min_stratum_samples=5)


# 1. Calibration feature representation extraction
def test_extraction_to_calibrator_features_representation():
    """Extraction dict is converted to CalibratorFeatures with correct attributes."""
    ext = _sample_extraction(
        field_name="owner_name",
        token_conf=0.88,
        engine="hwr",
    )
    val_results = [
        {"fields": ["ext-456"], "validator": "syntax", "verdict": "pass"},
        {"fields": ["other"], "validator": "checksum", "verdict": "fail"},
    ]
    feats = extraction_to_calibrator_features(
        extraction=ext,
        validation_results=val_results,
        script="kannada",
        doc_type="ror",
        legibility_band="good",
        writer_cluster_id="cluster_99",
    )

    assert feats.token_confidence == 0.88
    assert feats.field_class == "owner_name"
    assert feats.script == "kannada"
    assert feats.doc_type == "ror"
    assert feats.print_or_handwriting == "handwritten"
    assert feats.legibility_band == "good"
    assert feats.writer_cluster_id == "cluster_99"
    assert feats.validator_outcomes == {"syntax": "pass"}


# 2. Raw confidence preserved distinct from calibrated confidence
def test_raw_confidence_preserved_distinct_from_calibrated():
    """token_confidence is preserved as raw confidence and not overwritten."""
    ext = _sample_extraction(token_conf=0.90)
    envelope = _sample_work_envelope()
    calibrator = DeterministicCalibratorModel(lambda f: (0.82, {"token": 0.82}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, calib_res, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calibrator,
        threshold_policy=policy,
        trace_id="doc_1:page_1",
    )

    assert updated_ext["token_confidence"] == 0.90
    assert updated_ext["calibrated_confidence"] == 0.82
    assert updated_ext["calibrated_confidence"] != updated_ext["token_confidence"]
    assert 0.0 <= updated_ext["calibrated_confidence"] <= 1.0


# 3. Canonical 5-part stratum preserved
def test_canonical_stratum_passed_and_preserved():
    """Canonical 5-part stratum key is constructed using domain.stratum.stratum_key."""
    ext = _sample_extraction(field_name="parcel_no", engine="printed_ocr")
    feats = extraction_to_calibrator_features(
        extraction=ext,
        script="devanagari",
        doc_type="mutation",
        legibility_band="poor",
        writer_cluster_id="cluster_03",
    )
    expected = stratum_key(
        field_class="parcel_no",
        script="devanagari",
        print_or_handwriting="printed",
        legibility_band="poor",
        writer_cluster_id="cluster_03",
    )
    assert feats.canonical_stratum() == expected
    assert feats.canonical_stratum() == "parcel_no|devanagari|printed|poor|cluster_03"


# 4. Stratum-specific calibrator selected with sufficient evidence
def test_stratum_specific_calibrator_selected_with_sufficient_evidence():
    """When sample count >= min_stratum_samples, stratum-specific model is evaluated."""
    calibrator = _sample_trained_calibrator()
    ext = _sample_extraction(field_name="survey_number", engine="printed_ocr")
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)

    _, calib_res, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calibrator,
        threshold_policy=policy,
        trace_id="doc:page",
        script="devanagari",
        doc_type="ror",
        legibility_band="good",
        writer_cluster_id="cluster_01",
    )

    assert calib_res.regime == CalibrationRegime.STRATUM_SPECIFIC
    assert calib_res.stratum == "survey_number|devanagari|printed|good|cluster_01"


# 5. Pooled fallback selected with insufficient evidence
def test_pooled_fallback_selected_with_insufficient_evidence():
    """When sample count < min_stratum_samples, pooled fallback calibrator is selected."""
    labels = []
    for i in range(2):  # Only 2 samples (< 5)
        feat = CalibratorFeatures(token_confidence=0.8, field_class="tax_amount")
        labels.append(
            CorrectionLabel(
                extraction_id=f"e_{i}",
                is_correct=True,
                features=feat,
                stratum=feat.canonical_stratum(),
                source_page_digest=f"p_{i}",
            )
        )
    calibrator = train_per_stratum_calibrators(labels, min_stratum_samples=5)

    ext = _sample_extraction(field_name="tax_amount")
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)

    _, calib_res, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calibrator,
        threshold_policy=policy,
        trace_id="doc:page",
    )

    assert calib_res.regime == CalibrationRegime.POOLED_FALLBACK


# 6. Unseen stratum uses pooled fallback
def test_unseen_stratum_uses_pooled_fallback():
    """Unseen stratum without previous training evidence routes to pooled fallback."""
    calibrator = _sample_trained_calibrator()
    ext = _sample_extraction(field_name="completely_unseen_field")
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)

    _, calib_res, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calibrator,
        threshold_policy=policy,
        trace_id="doc:page",
    )

    assert calib_res.regime == CalibrationRegime.POOLED_FALLBACK


# 7. writer_cluster_id does not enter pooled feature encoding
def test_writer_cluster_id_not_in_pooled_feature_encoding():
    """writer_cluster_id is strictly stratum identity and is not encoded into model features."""
    calibrator = _sample_trained_calibrator()
    pooled_encoder = calibrator.pooled_model.encoder  # type: ignore[union-attr]

    assert all("cluster_01" not in name for name in pooled_encoder.feature_names)
    assert all("writer_cluster" not in name for name in pooled_encoder.feature_names)


# 8. Threshold policy determines routing outcome
def test_threshold_policy_determines_routing_outcome():
    """ThresholdPolicy dynamically determines auto_accept vs review without hardcoded 0.95."""
    # Strict policy: error target 0.01 -> required confidence 0.99
    strict_policy = ThresholdPolicy(target_error_rate=0.01)
    envelope = _sample_work_envelope()

    calib_high = DeterministicCalibratorModel(lambda f: (0.995, {}))
    ext = _sample_extraction()
    res_high, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib_high,
        threshold_policy=strict_policy,
        trace_id="doc:page",
    )
    assert res_high["routing_outcome"] == "auto_accept"

    calib_mid = DeterministicCalibratorModel(lambda f: (0.985, {}))
    res_mid, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib_mid,
        threshold_policy=strict_policy,
        trace_id="doc:page",
    )
    assert res_mid["routing_outcome"] == "review"


# 9. Cold Start SHADOW disables auto-accept
def test_cold_start_shadow_disables_auto_accept():
    """In SHADOW state, auto-accept is strictly disabled even with high calibrated confidence."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.999, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
        cold_start_state=ColdStartState.SHADOW,
    )

    assert updated_ext["routing_outcome"] == "review"
    assert updated_ext["routing_outcome"] != "auto_accept"


# 10. Cold Start REGRESSION disables auto-accept
def test_cold_start_regression_disables_auto_accept():
    """In REGRESSION state, auto-accept is strictly disabled and routes to review."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.999, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
        cold_start_state=ColdStartState.REGRESSION,
    )

    assert updated_ext["routing_outcome"] == "review"


# 11. Cold Start RAMP allows auto-accept with conservative posture
def test_cold_start_ramp_allows_auto_accept():
    """In RAMP state, auto-accept is allowed for high confidence fields."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.99, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
        cold_start_state=ColdStartState.RAMP,
    )

    assert updated_ext["routing_outcome"] == "auto_accept"


# 12. Cold Start STEADY standard operation
def test_cold_start_steady_standard_operation():
    """In STEADY state, normal auto-accept operates per threshold policy."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.96, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
        cold_start_state=ColdStartState.STEADY,
    )

    assert updated_ext["routing_outcome"] == "auto_accept"


# 13. Audit sampling routing
def test_audit_sampling_routing():
    """When selected for audit, high-confidence fields route to audit_sample (FR-CNF-07)."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.98, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
        force_audit_sample=True,
    )

    assert updated_ext["routing_outcome"] == "audit_sample"


# 14. Validator conflict routing
def test_validator_conflict_routing():
    """Failed validation results route the field to conflict (FR-CFL-01)."""
    ext = _sample_extraction(extraction_id="ext-fail")
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.99, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)
    val_results = [
        {"fields": ["ext-fail"], "validator": "arithmetic", "verdict": "fail"}
    ]

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
        validation_results=val_results,
    )

    assert updated_ext["routing_outcome"] == "conflict"


# 15. Upstream novelty outside regime routing
def test_upstream_novelty_outside_regime_routing():
    """Out-of-distribution / high novelty routes to outside_calibrated_regime (FR-CNF-14)."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.99, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
        is_outside_regime=True,
    )

    assert updated_ext["routing_outcome"] == "outside_calibrated_regime"


# 16. Pinned model and config provenance preserved
def test_pinned_model_and_config_provenance_preserved():
    """Pinned model_version and config_version from WorkEnvelope are preserved on Extraction."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope(
        calibrator_version="pinned_calibrator_v3",
        config_version="pinned_cfg_v42",
    )
    calib = DeterministicCalibratorModel(lambda f: (0.85, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    updated_ext, _, _ = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="doc:page",
    )

    assert updated_ext["model_version"] == "pinned_calibrator_v3"
    assert updated_ext["config_version"] == "pinned_cfg_v42"


# 17. DecisionPublisher envelope structure
def test_decision_publisher_envelope_structure():
    """DecisionPublisher correctly emits DecisionEnvelope matching asyncapi specification."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.97, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)
    trace_id = "doc-uuid:page-uuid"

    _, _, published = process_extraction_for_decision(
        extraction=ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id=trace_id,
        publish_to_queue=True,
    )

    assert published is not None
    assert published["producer"] == "confidence-novelty"
    assert published["trace_id"] == trace_id
    assert published["_queue"] == "DECISION_QUEUE"
    assert published["work_envelope"] == envelope
    assert published["payload"]["routing_outcome"] == "auto_accept"
    assert published["payload"]["calibrated_confidence"] == 0.97


# 18. Deterministic execution
def test_deterministic_decision_flow_execution():
    """Identical input and pinned envelope produce identical decisions and scores."""
    ext = _sample_extraction()
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.91, {"token": 0.91}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    u1, c1, _ = process_extraction_for_decision(
        extraction=ext, work_envelope=envelope, calibrator=calib, threshold_policy=policy, trace_id="t1", publish_to_queue=False
    )
    u2, c2, _ = process_extraction_for_decision(
        extraction=ext, work_envelope=envelope, calibrator=calib, threshold_policy=policy, trace_id="t1", publish_to_queue=False
    )

    assert u1 == u2
    assert c1.calibrated_confidence == c2.calibrated_confidence
    assert c1.feature_attributions == c2.feature_attributions


# 19. Invalid inputs rejected explicitly
def test_invalid_inputs_rejected_explicitly():
    """Invalid extraction or envelope structures are rejected with ValueError."""
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.9, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    # Missing extraction id
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"field_name": "x", "token_confidence": 0.9}, envelope, calib, policy, "trace"
        )

    # Missing field_name
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"id": "e1", "token_confidence": 0.9}, envelope, calib, policy, "trace"
        )

    # Missing token_confidence
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"id": "e1", "field_name": "x"}, envelope, calib, policy, "trace"
        )

    # Negative token_confidence
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"id": "e1", "field_name": "x", "token_confidence": -0.1}, envelope, calib, policy, "trace"
        )

    # Greater than 1.0 token_confidence
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"id": "e1", "field_name": "x", "token_confidence": 1.05}, envelope, calib, policy, "trace"
        )

    # NaN token_confidence
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"id": "e1", "field_name": "x", "token_confidence": float("nan")}, envelope, calib, policy, "trace"
        )

    # Unavailable calibrator model
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            _sample_extraction(), envelope, None, policy, "trace"  # type: ignore[arg-type]
        )

    # Missing pinned versions in work_envelope
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            _sample_extraction(), {"envelope_id": "1"}, calib, policy, "trace"
        )


# 20. Exact threshold boundary conditions (FR-CNF-03)
def test_threshold_policy_exact_boundary_conditions():
    """Verify deterministic routing below, exactly at, and above the required confidence threshold."""
    # error target 0.05 -> required confidence 0.95 (1.0 - 0.05)
    policy = ThresholdPolicy(target_error_rate=0.05)
    req = policy.required_confidence
    assert req == 0.95

    envelope = _sample_work_envelope()
    ext = _sample_extraction()

    # Below threshold: 0.949999 -> review
    calib_below = DeterministicCalibratorModel(lambda f: (0.949999, {}))
    res_below, _, _ = process_extraction_for_decision(
        extraction=ext, work_envelope=envelope, calibrator=calib_below, threshold_policy=policy, trace_id="trace"
    )
    assert res_below["routing_outcome"] == "review"

    # Exactly at threshold: 0.95 -> auto_accept
    calib_exact = DeterministicCalibratorModel(lambda f: (0.95, {}))
    res_exact, _, _ = process_extraction_for_decision(
        extraction=ext, work_envelope=envelope, calibrator=calib_exact, threshold_policy=policy, trace_id="trace"
    )
    assert res_exact["routing_outcome"] == "auto_accept"

    # Above threshold: 0.950001 -> auto_accept
    calib_above = DeterministicCalibratorModel(lambda f: (0.950001, {}))
    res_above, _, _ = process_extraction_for_decision(
        extraction=ext, work_envelope=envelope, calibrator=calib_above, threshold_policy=policy, trace_id="trace"
    )
    assert res_above["routing_outcome"] == "auto_accept"


# 21. Routing outcomes match contracts schema (contracts/schemas/extraction.schema.json)
def test_routing_outcomes_match_contracts_schema():
    """Verify all emitted routing outcomes match the valid enum in extraction.schema.json."""
    schema_path = Path("contracts/schemas/extraction.schema.json")
    if not schema_path.exists():
        schema_path = Path(__file__).resolve().parents[4] / "contracts" / "schemas" / "extraction.schema.json"

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    allowed_outcomes = set(schema["properties"]["routing_outcome"]["enum"])
    expected_non_null = {"auto_accept", "audit_sample", "review", "conflict", "outside_calibrated_regime"}
    assert allowed_outcomes == expected_non_null | {None}

    envelope = _sample_work_envelope()
    ext = _sample_extraction()
    policy = ThresholdPolicy(target_error_rate=0.05)

    # 1. auto_accept
    calib_high = DeterministicCalibratorModel(lambda f: (0.98, {}))
    res, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t")
    assert res["routing_outcome"] in allowed_outcomes
    assert res["routing_outcome"] == "auto_accept"

    # 2. review
    calib_low = DeterministicCalibratorModel(lambda f: (0.80, {}))
    res, _, _ = process_extraction_for_decision(ext, envelope, calib_low, policy, "t")
    assert res["routing_outcome"] in allowed_outcomes
    assert res["routing_outcome"] == "review"

    # 3. audit_sample
    res, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t", force_audit_sample=True)
    assert res["routing_outcome"] in allowed_outcomes
    assert res["routing_outcome"] == "audit_sample"

    # 4. conflict
    res, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t", is_conflict=True)
    assert res["routing_outcome"] in allowed_outcomes
    assert res["routing_outcome"] == "conflict"

    # 5. outside_calibrated_regime
    res, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t", is_outside_regime=True)
    assert res["routing_outcome"] in allowed_outcomes
    assert res["routing_outcome"] == "outside_calibrated_regime"


# 22. Exhaustive Cold Start posture routing across all states (FR-CNF-15)
def test_cold_start_exhaustive_posture_routing():
    """Verify routing outcome for candidates above and below threshold across all 4 operational states."""
    envelope = _sample_work_envelope()
    ext = _sample_extraction()
    policy = ThresholdPolicy(target_error_rate=0.05)  # threshold 0.95
    calib_high = DeterministicCalibratorModel(lambda f: (0.99, {}))
    calib_low = DeterministicCalibratorModel(lambda f: (0.85, {}))

    # SHADOW: Auto-accept strictly disabled -> always review
    res_sh_hi, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t", cold_start_state=ColdStartState.SHADOW)
    res_sh_lo, _, _ = process_extraction_for_decision(ext, envelope, calib_low, policy, "t", cold_start_state=ColdStartState.SHADOW)
    assert res_sh_hi["routing_outcome"] == "review"
    assert res_sh_lo["routing_outcome"] == "review"

    # RAMP: Auto-accept enabled
    res_rm_hi, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t", cold_start_state=ColdStartState.RAMP)
    res_rm_lo, _, _ = process_extraction_for_decision(ext, envelope, calib_low, policy, "t", cold_start_state=ColdStartState.RAMP)
    assert res_rm_hi["routing_outcome"] == "auto_accept"
    assert res_rm_lo["routing_outcome"] == "review"

    # STEADY: Standard auto-accept enabled
    res_st_hi, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t", cold_start_state=ColdStartState.STEADY)
    res_st_lo, _, _ = process_extraction_for_decision(ext, envelope, calib_low, policy, "t", cold_start_state=ColdStartState.STEADY)
    assert res_st_hi["routing_outcome"] == "auto_accept"
    assert res_st_lo["routing_outcome"] == "review"

    # REGRESSION: Auto-accept strictly disabled -> always review
    res_rg_hi, _, _ = process_extraction_for_decision(ext, envelope, calib_high, policy, "t", cold_start_state=ColdStartState.REGRESSION)
    res_rg_lo, _, _ = process_extraction_for_decision(ext, envelope, calib_low, policy, "t", cold_start_state=ColdStartState.REGRESSION)
    assert res_rg_hi["routing_outcome"] == "review"
    assert res_rg_lo["routing_outcome"] == "review"


# 23. Audit sampling provisional policy parameters and selector
def test_audit_sampling_provisional_policy_parameters():
    """Verify AuditSamplingPolicy defaults and provisional deterministic selector callback."""
    asp = AuditSamplingPolicy()
    assert asp.standard_rate == 0.05
    assert asp.elevated_rate == 0.20

    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)
    calib = DeterministicCalibratorModel(lambda f: (0.98, {}))

    selector = lambda ext_id: ext_id.endswith("even")

    ext_even = _sample_extraction(extraction_id="ext-even")
    ext_odd = _sample_extraction(extraction_id="ext-odd")

    res_even, _, _ = process_extraction_for_decision(
        ext_even, envelope, calib, policy, "t", audit_selector=selector
    )
    res_odd, _, _ = process_extraction_for_decision(
        ext_odd, envelope, calib, policy, "t", audit_selector=selector
    )

    assert res_even["routing_outcome"] == "audit_sample"
    assert res_odd["routing_outcome"] == "auto_accept"


# 24. Extended input validation and failure modes
def test_invalid_inputs_rejected_extended():
    """Verify rejection of non-finite infinity, missing config_version, and missing calibrator version."""
    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.9, {}))
    policy = ThresholdPolicy(target_error_rate=0.05)

    # Positive Infinity in token_confidence
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"id": "e1", "field_name": "x", "token_confidence": float("inf")}, envelope, calib, policy, "trace"
        )

    # Negative Infinity in token_confidence
    with pytest.raises(ValueError):
        process_extraction_for_decision(
            {"id": "e1", "field_name": "x", "token_confidence": float("-inf")}, envelope, calib, policy, "trace"
        )

    # Missing config_version in work_envelope
    env_missing_cfg = dict(envelope)
    del env_missing_cfg["config_version"]
    with pytest.raises(ValueError):
        process_extraction_for_decision(_sample_extraction(), env_missing_cfg, calib, policy, "trace")

    # Missing confidence_calibrator in model_versions
    env_missing_calib = dict(envelope)
    env_missing_calib["model_versions"] = {"printed_ocr": "v1"}
    with pytest.raises(ValueError):
        process_extraction_for_decision(_sample_extraction(), env_missing_calib, calib, policy, "trace")

    # Empty or non-string trace_id
    with pytest.raises(ValueError):
        process_extraction_for_decision(_sample_extraction(), envelope, calib, policy, "")


if __name__ == "__main__":
    test_extraction_to_calibrator_features_representation()
    test_raw_confidence_preserved_distinct_from_calibrated()
    test_canonical_stratum_passed_and_preserved()
    test_stratum_specific_calibrator_selected_with_sufficient_evidence()
    test_pooled_fallback_selected_with_insufficient_evidence()
    test_unseen_stratum_uses_pooled_fallback()
    test_writer_cluster_id_not_in_pooled_feature_encoding()
    test_threshold_policy_determines_routing_outcome()
    test_cold_start_shadow_disables_auto_accept()
    test_cold_start_regression_disables_auto_accept()
    test_cold_start_ramp_allows_auto_accept()
    test_cold_start_steady_standard_operation()
    test_audit_sampling_routing()
    test_validator_conflict_routing()
    test_upstream_novelty_outside_regime_routing()
    test_pinned_model_and_config_provenance_preserved()
    test_decision_publisher_envelope_structure()
    test_deterministic_decision_flow_execution()
    test_invalid_inputs_rejected_explicitly()
    test_threshold_policy_exact_boundary_conditions()
    test_routing_outcomes_match_contracts_schema()
    test_cold_start_exhaustive_posture_routing()
    test_audit_sampling_provisional_policy_parameters()
    test_invalid_inputs_rejected_extended()
    print("All 24 decision flow integration unit tests passed successfully.")
