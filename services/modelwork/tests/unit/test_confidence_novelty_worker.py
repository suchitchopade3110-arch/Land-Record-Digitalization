"""Unit tests for Confidence + Novelty Queue Consumer Worker (FR-CNF-01..04, FR-CNF-14..15, FR-CFL-01).

Tests A through P verify:
- Test A: Valid message with producer='validation' processes extraction and emits DecisionEnvelope.
- Test B: Valid message with producer='entity-res' processes extraction and emits DecisionEnvelope.
- Test C: Unsupported producers ('triage', 'ingest', 'confidence-novelty', unknown) rejected with ValueError.
- Test D: Novelty evaluated FIRST (FR-CNF-14): high novelty routes to outside_calibrated_regime and gates calibration.
- Test E: Validator failure verdict routes to conflict outcome (FR-CFL-01).
- Test F: In-regime + high confidence in STEADY posture routes to auto_accept.
- Test G: In-regime + low confidence routes to review.
- Test H: Operational posture enforcement: in SHADOW or REGRESSION, even high confidence routes to review.
- Test I: Audit sample selection: force_audit_sample=True routes eligible high-confidence to audit_sample.
- Test J: Pinned provenance preservation: model_version, config_version, trace_id propagated; missing versions rejected.
- Test K: Raw token_confidence is preserved without mutation.
- Test L: Missing extraction handling: missing extraction ID fails safely without state corruption.
- Test M: Page context hydration: correctly extracts script, doc_type, legibility_band, writer_cluster_id.
- Test N: Multi-extraction batching: processes multiple extractions referenced across validation results.
- Test O: Empty extraction list handling: returns clean summary.
- Test P: Replay idempotency: repeated execution produces deterministic output.
"""
from __future__ import annotations

import math
import sys
import uuid
from typing import Any
from unittest.mock import MagicMock

try:
    import pytest
except ImportError:
    class _MockPytest:
        @staticmethod
        def raises(exc_type: Any, match: str | None = None) -> Any:
            class RaisesContext:
                def __init__(self, exc_type: Any, match: str | None = None) -> None:
                    self.exc_type = exc_type
                    self.match = match
                    self.value: Any = None
                def __enter__(self) -> RaisesContext:
                    return self
                def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
                    if exc_type is None:
                        raise AssertionError(f"DID NOT RAISE {self.exc_type}")
                    self.value = exc_val
                    if self.match and self.match not in str(exc_val):
                        raise AssertionError(f"'{self.match}' not in '{exc_val}'")
                    return issubclass(exc_type, self.exc_type)
            return RaisesContext(exc_type, match)
    pytest = _MockPytest()  # type: ignore[assignment]

from modelwork.domain.calibration.calibrator import (
    CalibrationRegime,
    CalibratorFeatures,
    CalibratorModel,
    LogisticRegressionCalibrator,
    ThresholdPolicy,
)
from modelwork.domain.calibration.cold_start import ColdStartState
from modelwork.domain.calibration.novelty import (
    CentroidDistanceNoveltyModel,
    DeterministicNoveltyModel,
    NoveltyDetector,
    NoveltyFeatures,
    NoveltyPolicy,
    NoveltyResult,
)
from modelwork.workers.confidence_novelty import (
    DEFAULT_THRESHOLD_POLICY,
    VALID_PRODUCERS,
    handle,
)


class MockExtraction:
    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", "ext-" + str(uuid.uuid4())[:8])
        self.page_id = kwargs.get("page_id", "page-" + str(uuid.uuid4())[:8])
        self.field_name = kwargs.get("field_name", "survey_number")
        self.raw_value = kwargs.get("raw_value", "102/4A")
        self.canonical_value = kwargs.get("canonical_value", "102/4A")
        self.unconstrained_value = kwargs.get("unconstrained_value", "102/4A")
        self.bbox = kwargs.get("bbox", {"x": 10, "y": 20, "w": 30, "h": 40})
        self.engine = kwargs.get("engine", "printed_ocr")
        self.model_version = kwargs.get("model_version", "ocr_v1")
        self.config_version = kwargs.get("config_version", "cfg_v1")
        self.token_confidence = kwargs.get("token_confidence", 0.96)
        self.calibrated_confidence = kwargs.get("calibrated_confidence", None)
        self.novelty_score = kwargs.get("novelty_score", None)
        self.routing_outcome = kwargs.get("routing_outcome", None)
        self.entry_status = kwargs.get("entry_status", "unknown")
        self.attestation_refs = kwargs.get("attestation_refs", None)
        self.top_k = kwargs.get("top_k", None)

    def to_contract_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "page_id": self.page_id,
            "field_name": self.field_name,
            "raw_value": self.raw_value,
            "canonical_value": self.canonical_value,
            "unconstrained_value": self.unconstrained_value,
            "bbox": self.bbox,
            "engine": self.engine,
            "model_version": self.model_version,
            "config_version": self.config_version,
            "token_confidence": self.token_confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "novelty_score": self.novelty_score,
            "routing_outcome": self.routing_outcome,
            "entry_status": self.entry_status,
            "attestation_refs": self.attestation_refs,
            "top_k": self.top_k,
        }


class MockPage:
    def __init__(self, **kwargs: Any) -> None:
        self.id = kwargs.get("id", "page-01")
        self.document_id = kwargs.get("document_id", "doc-01")
        self.index = kwargs.get("index", 1)
        self.script = kwargs.get("script", "kannada")
        self.language = kwargs.get("language", "kan")
        self.doc_type = kwargs.get("doc_type", "ror")
        self.page_role = kwargs.get("page_role", "tabular_register")
        self.legibility_band = kwargs.get("legibility_band", "good")
        self.writer_cluster_id = kwargs.get("writer_cluster_id", str(uuid.uuid4()))
        self.quality_score = kwargs.get("quality_score", 0.92)


class MockSession:
    def __init__(self) -> None:
        self.extractions: dict[str, MockExtraction] = {}
        self.pages: dict[str, MockPage] = {}
        self.flushed = False

    def get(self, entity_cls: Any, ident: str) -> Any:
        cls_name = getattr(entity_cls, "__name__", str(entity_cls))
        if "Extraction" in cls_name:
            return self.extractions.get(ident)
        if "Page" in cls_name:
            return self.pages.get(ident)
        return None

    def flush(self) -> None:
        self.flushed = True


class ConstantCalibrator(CalibratorModel):
    def __init__(self, score: float = 0.98) -> None:
        self.score = score

    def predict_calibrated_confidence(self, features: CalibratorFeatures) -> tuple[float, dict[str, float]]:
        return self.score, {"token_confidence": 0.1}


def _make_valid_message(
    producer: str = "validation",
    ext_ids: list[str] | None = None,
    verdicts: list[str] | None = None,
    with_novelty: bool = False,
) -> dict[str, Any]:
    ids = ext_ids or ["ext-valid-01"]
    v_list = verdicts or ["pass"] * len(ids)

    validation_results = []
    for i, eid in enumerate(ids):
        validation_results.append({
            "id": str(uuid.uuid4()),
            "record_id": str(uuid.uuid4()),
            "validator": "syntactic",
            "verdict": v_list[i],
            "reason_code": "VALID_FORMAT" if v_list[i] == "pass" else "SYNTAX_ERROR",
            "fields": [eid],
            "config_version": "cfg-v1.0",
            "consumed_constraints": [],
        })

    model_versions = {"confidence_calibrator": "calibrator_v1.0"}
    if with_novelty:
        model_versions["novelty_detector"] = "novelty_v1.0"

    return {
        "message_id": str(uuid.uuid4()),
        "trace_id": "trace:doc:page:01",
        "emitted_at": "2026-09-11T10:00:00Z",
        "producer": producer,
        "work_envelope": {
            "document_id": "doc-01",
            "page_id": "page-01",
            "trace_id": "trace:doc:page:01",
            "model_versions": model_versions,
            "config_version": "cfg-v1.0",
        },
        "payload": {
            "validation_results": validation_results,
            "dedup_match_candidates": [],
        },
    }


def test_a_producer_validation_processes_and_emits_decision():
    """Valid message with producer='validation' processes extraction and emits DecisionEnvelope to DECISION_QUEUE."""
    ext = MockExtraction(id="ext-test-a", token_confidence=0.98)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    calibrator = ConstantCalibrator(0.98)

    result = handle(msg, session=session, calibrator=calibrator)

    assert result["status"] == "ok"
    assert result["processed_count"] == 1
    assert len(result["decisions"]) == 1

    decision_env = result["decisions"][0]
    assert decision_env["producer"] == "confidence-novelty"
    assert decision_env["_queue"] == "DECISION_QUEUE"
    assert decision_env["payload"]["id"] == ext.id
    assert decision_env["payload"]["routing_outcome"] == "auto_accept"
    assert decision_env["payload"]["calibrated_confidence"] == 0.98
    assert ext.routing_outcome == "auto_accept"
    assert ext.calibrated_confidence == 0.98
    assert session.flushed is True


def test_b_producer_entity_res_processes_and_emits_decision():
    """Valid message with producer='entity-res' is accepted per queue contract."""
    ext = MockExtraction(id="ext-test-b", token_confidence=0.97)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="entity-res", ext_ids=[ext.id])
    calibrator = ConstantCalibrator(0.97)

    result = handle(msg, session=session, calibrator=calibrator)

    assert result["status"] == "ok"
    assert result["processed_count"] == 1
    decision_env = result["decisions"][0]
    assert decision_env["producer"] == "confidence-novelty"
    assert decision_env["payload"]["routing_outcome"] == "auto_accept"


def test_c_unsupported_producers_rejected():
    """Unsupported producers ('triage', 'ingest', 'confidence-novelty', 'unknown') rejected with ValueError."""
    ext = MockExtraction(id="ext-test-c")
    session = MockSession()
    session.extractions[ext.id] = ext

    for bad_prod in ["triage", "ingest", "confidence-novelty", "reviewer", "unknown"]:
        msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
        msg["producer"] = bad_prod
        with pytest.raises(ValueError, match="Unsupported producer"):
            handle(msg, session=session)


def test_d_novelty_first_evaluation_gates_calibration():
    """Novelty evaluated FIRST (FR-CNF-14): high novelty routes to outside_calibrated_regime and gates auto_accept."""
    ext = MockExtraction(id="ext-test-d", token_confidence=0.99)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id], with_novelty=True)

    detector = DeterministicNoveltyModel(
        evaluation_fn=lambda f: NoveltyResult(
            novelty_score=0.85,
            is_outside_regime=True,
            regime_reason="novel_feature_vector",
        )
    )

    calibrator = ConstantCalibrator(0.99)

    result = handle(
        msg,
        session=session,
        calibrator=calibrator,
        novelty_detector=detector,
    )

    decision_env = result["decisions"][0]
    assert decision_env["payload"]["routing_outcome"] == "outside_calibrated_regime"
    assert decision_env["payload"]["novelty_score"] > 0.30
    assert ext.routing_outcome == "outside_calibrated_regime"


def test_e_validator_failure_routes_to_conflict():
    """Validator failure verdict routes to conflict outcome (FR-CFL-01)."""
    ext = MockExtraction(id="ext-test-e", token_confidence=0.98)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id], verdicts=["fail"])
    calibrator = ConstantCalibrator(0.98)

    result = handle(msg, session=session, calibrator=calibrator)

    decision_env = result["decisions"][0]
    assert decision_env["payload"]["routing_outcome"] == "conflict"
    assert ext.routing_outcome == "conflict"


def test_f_high_confidence_steady_posture_auto_accept():
    """In-regime + high confidence meeting threshold in STEADY posture routes to auto_accept."""
    ext = MockExtraction(id="ext-test-f", token_confidence=0.96)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    thresh = ThresholdPolicy(target_error_rate=0.05)
    calibrator = ConstantCalibrator(0.96)

    result = handle(msg, session=session, calibrator=calibrator, threshold_policy=thresh)

    decision_env = result["decisions"][0]
    assert decision_env["payload"]["routing_outcome"] == "auto_accept"
    assert decision_env["payload"]["calibrated_confidence"] == 0.96


def test_g_low_confidence_routes_to_review():
    """In-regime + low confidence below threshold routes to review."""
    ext = MockExtraction(id="ext-test-g", token_confidence=0.80)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    thresh = ThresholdPolicy(target_error_rate=0.05)
    calibrator = ConstantCalibrator(0.80)

    result = handle(msg, session=session, calibrator=calibrator, threshold_policy=thresh)

    decision_env = result["decisions"][0]
    assert decision_env["payload"]["routing_outcome"] == "review"
    assert ext.routing_outcome == "review"


def test_h_shadow_and_regression_posture_routes_to_review():
    """Operational posture enforcement: in SHADOW or REGRESSION, auto_accept is strictly disabled."""
    ext = MockExtraction(id="ext-test-h", token_confidence=0.99)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    calibrator = ConstantCalibrator(0.99)

    for posture_state in [ColdStartState.SHADOW, ColdStartState.REGRESSION]:
        result = handle(msg, session=session, calibrator=calibrator, cold_start_state=posture_state)
        decision_env = result["decisions"][0]
        assert decision_env["payload"]["routing_outcome"] == "review"


def test_i_audit_sample_selection():
    """force_audit_sample=True routes eligible high-confidence extraction to audit_sample."""
    ext = MockExtraction(id="ext-test-i", token_confidence=0.98)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    calibrator = ConstantCalibrator(0.98)

    result = handle(msg, session=session, calibrator=calibrator, force_audit_sample=True)

    decision_env = result["decisions"][0]
    assert decision_env["payload"]["routing_outcome"] == "audit_sample"
    assert ext.routing_outcome == "audit_sample"


def test_j_pinned_provenance_preservation_and_rejection_of_missing():
    """Pinned provenance (model_version, config_version, trace_id) preserved; missing versions rejected."""
    ext = MockExtraction(id="ext-test-j")
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    msg["work_envelope"]["model_versions"]["confidence_calibrator"] = "calib_v2.4.0"
    msg["work_envelope"]["config_version"] = "cfg_v9.9"
    msg["trace_id"] = "trace:test:j:99"

    calibrator = ConstantCalibrator(0.96)
    result = handle(msg, session=session, calibrator=calibrator)

    decision_env = result["decisions"][0]
    assert decision_env["trace_id"] == "trace:test:j:99"
    assert decision_env["payload"]["model_version"] == "calib_v2.4.0"
    assert decision_env["payload"]["config_version"] == "cfg_v9.9"
    assert ext.model_version == "calib_v2.4.0"
    assert ext.config_version == "cfg_v9.9"

    bad_msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    del bad_msg["work_envelope"]["model_versions"]["confidence_calibrator"]
    with pytest.raises(ValueError, match="missing pinned 'confidence_calibrator'"):
        handle(bad_msg, session=session, calibrator=calibrator)

    bad_msg2 = _make_valid_message(producer="validation", ext_ids=[ext.id])
    del bad_msg2["work_envelope"]["config_version"]
    with pytest.raises(ValueError, match="missing pinned 'config_version'"):
        handle(bad_msg2, session=session, calibrator=calibrator)


def test_k_raw_token_confidence_preserved():
    """Raw token_confidence is never mutated by calibration."""
    ext = MockExtraction(id="ext-test-k", token_confidence=0.888)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    calibrator = ConstantCalibrator(0.97)

    result = handle(msg, session=session, calibrator=calibrator)

    decision_env = result["decisions"][0]
    assert decision_env["payload"]["token_confidence"] == 0.888
    assert decision_env["payload"]["calibrated_confidence"] == 0.97
    assert ext.token_confidence == 0.888
    assert ext.calibrated_confidence == 0.97


def test_l_missing_extraction_rejected_safely():
    """Missing extraction in session and loader raises ValueError without corrupting state."""
    session = MockSession()
    msg = _make_valid_message(producer="validation", ext_ids=["non-existent-ext"])

    with pytest.raises(ValueError, match="could not be resolved"):
        handle(msg, session=session)


def test_m_page_context_hydration():
    """Worker hydrates script, doc_type, legibility_band, writer_cluster_id from Page entity."""
    page = MockPage(
        id="page-m-01",
        script="devanagari",
        doc_type="khasra_khatauni",
        legibility_band="poor",
        writer_cluster_id="00000000-0000-0000-0000-000000000099",
    )
    ext = MockExtraction(id="ext-test-m", page_id=page.id, token_confidence=0.96)
    session = MockSession()
    session.pages[page.id] = page
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])

    captured_stratum: list[str] = []
    class FeatureCapturingCalibrator(CalibratorModel):
        def predict_calibrated_confidence(self, features: CalibratorFeatures) -> tuple[float, dict[str, float]]:
            captured_stratum.append(features.canonical_stratum())
            assert features.script == "devanagari"
            assert features.doc_type == "khasra_khatauni"
            assert features.legibility_band == "poor"
            assert features.writer_cluster_id == "00000000-0000-0000-0000-000000000099"
            return 0.96, {}

    handle(msg, session=session, calibrator=FeatureCapturingCalibrator())
    assert len(captured_stratum) == 1


def test_n_multi_extraction_batching():
    """Message referencing multiple extractions processes all extractions independently."""
    ext1 = MockExtraction(id="ext-batch-01", token_confidence=0.98)
    ext2 = MockExtraction(id="ext-batch-02", token_confidence=0.60)
    session = MockSession()
    session.extractions[ext1.id] = ext1
    session.extractions[ext2.id] = ext2

    msg = _make_valid_message(
        producer="validation",
        ext_ids=[ext1.id, ext2.id],
        verdicts=["pass", "pass"],
    )

    class CustomCalibrator(CalibratorModel):
        def predict_calibrated_confidence(self, features: CalibratorFeatures) -> tuple[float, dict[str, float]]:
            return features.token_confidence, {}

    result = handle(msg, session=session, calibrator=CustomCalibrator())

    assert result["processed_count"] == 2
    assert len(result["decisions"]) == 2

    d1 = next(d for d in result["decisions"] if d["payload"]["id"] == ext1.id)
    d2 = next(d for d in result["decisions"] if d["payload"]["id"] == ext2.id)

    assert d1["payload"]["routing_outcome"] == "auto_accept"
    assert d2["payload"]["routing_outcome"] == "review"
    assert ext1.routing_outcome == "auto_accept"
    assert ext2.routing_outcome == "review"


def test_o_empty_extractions_returns_clean_summary():
    """Message with 0 referenced extractions returns clean empty summary without error."""
    msg = _make_valid_message(producer="validation", ext_ids=[])
    msg["payload"]["validation_results"] = []

    result = handle(msg, session=MockSession())
    assert result["status"] == "ok"
    assert result["processed_count"] == 0
    assert result["decisions"] == []


def test_p_replay_idempotency():
    """Repeated execution of handle with same message produces deterministic output and identical DB state."""
    ext = MockExtraction(id="ext-test-p", token_confidence=0.96)
    session = MockSession()
    session.extractions[ext.id] = ext

    msg = _make_valid_message(producer="validation", ext_ids=[ext.id])
    calibrator = ConstantCalibrator(0.96)

    res1 = handle(msg, session=session, calibrator=calibrator)
    state1 = (ext.calibrated_confidence, ext.routing_outcome, ext.model_version)

    res2 = handle(msg, session=session, calibrator=calibrator)
    state2 = (ext.calibrated_confidence, ext.routing_outcome, ext.model_version)

    assert state1 == state2 == (0.96, "auto_accept", "calibrator_v1.0")
    assert res1["decisions"][0]["payload"] == res2["decisions"][0]["payload"]


if __name__ == "__main__":
    test_a_producer_validation_processes_and_emits_decision()
    test_b_producer_entity_res_processes_and_emits_decision()
    test_c_unsupported_producers_rejected()
    test_d_novelty_first_evaluation_gates_calibration()
    test_e_validator_failure_routes_to_conflict()
    test_f_high_confidence_steady_posture_auto_accept()
    test_g_low_confidence_routes_to_review()
    test_h_shadow_and_regression_posture_routes_to_review()
    test_i_audit_sample_selection()
    test_j_pinned_provenance_preservation_and_rejection_of_missing()
    test_k_raw_token_confidence_preserved()
    test_l_missing_extraction_rejected_safely()
    test_m_page_context_hydration()
    test_n_multi_extraction_batching()
    test_o_empty_extractions_returns_clean_summary()
    test_p_replay_idempotency()
    print("All 16 Confidence + Novelty Worker unit tests passed successfully.")
