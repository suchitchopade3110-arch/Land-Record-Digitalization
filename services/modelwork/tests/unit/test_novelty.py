"""Unit tests for P0 Novelty Detection foundation and contract boundary (FR-CNF-14).

Tests A through I verify:
- Test A: In-regime input produces novelty_score <= threshold, is_outside_regime=False.
- Test B: Novel input produces novelty_score > threshold, is_outside_regime=True, explainable regime_reason.
- Test C: Determinism with identical inputs/prototypes.
- Test D: Confidence independence (varying token_confidence has zero effect on novelty).
- Test E: Pinned provenance (novelty_detector version, config_version, trace_id).
- Test F: Exact configured boundary behavior (score == threshold vs score > threshold).
- Test G: Rejection of invalid inputs (NaN, +Inf, -Inf, mismatched dimensions, invalid threshold).
- Test H: Decision flow integration (novelty_result -> outside_calibrated_regime -> DecisionEnvelope).
- Test I: Backend integration (verifying outside_calibrated_regime and cluster dedup in backend engine).
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

# Shim optional third-party packages if not present in the active Python environment
def _make_mock_pkg(name: str) -> MagicMock:
    m = MagicMock()
    m.__path__ = []
    m.__file__ = f"{name}/__init__.py"
    return m

for _mod in [
    "sqlalchemy",
    "sqlalchemy.orm",
    "sqlalchemy.dialects",
    "sqlalchemy.dialects.postgresql",
    "landaudit",
    "structlog",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = _make_mock_pkg(_mod)

class MockColumn:
    def __ge__(self, other: Any) -> MockColumn: return self
    def __le__(self, other: Any) -> MockColumn: return self
    def __eq__(self, other: Any) -> MockColumn: return self  # type: ignore[override]
    def __ne__(self, other: Any) -> MockColumn: return self  # type: ignore[override]
    def desc(self) -> MockColumn: return self
    def in_(self, other: Any) -> MockColumn: return self

class MockQuery:
    def __init__(self) -> None:
        self.clauses: list[Any] = []
        self.order_by_clauses: list[Any] = []
        self.limit_val: int | None = None

    def where(self, *a: Any, **kw: Any) -> MockQuery:
        self.clauses.extend(a)
        return self

    def order_by(self, *a: Any, **kw: Any) -> MockQuery:
        self.order_by_clauses.extend(a)
        return self

    def limit(self, val: int) -> MockQuery:
        self.limit_val = val
        return self

class DeclarativeBase:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)

sys.modules["sqlalchemy.orm"].DeclarativeBase = DeclarativeBase
sys.modules["sqlalchemy.orm"].mapped_column = lambda *a, **kw: MockColumn()
sys.modules["sqlalchemy"].select = lambda *a, **kw: MockQuery()

if "pydantic" not in sys.modules:
    class BaseModel:
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self) -> dict[str, Any]:
            return dict(self.__dict__)

        def dict(self) -> dict[str, Any]:
            return dict(self.__dict__)

    m_pyd = _make_mock_pkg("pydantic")
    m_pyd.BaseModel = BaseModel
    sys.modules["pydantic"] = m_pyd

try:
    import pytest
except ImportError:
    class _MockPytest:
        @staticmethod
        def raises(expected_exception: type[BaseException], match: str | None = None) -> Any:
            class _RaisesContext:
                def __enter__(self) -> _RaisesContext:
                    return self
                def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
                    if exc_type is None:
                        raise AssertionError(f"Expected {expected_exception.__name__} was not raised")
                    if not issubclass(exc_type, expected_exception):
                        return False
                    if match and match not in str(exc_val):
                        raise AssertionError(f"Exception message '{exc_val}' does not match '{match}'")
                    return True
            return _RaisesContext()
    pytest = _MockPytest()  # type: ignore[assignment]

from modelwork.domain.calibration.calibrator import (
    DeterministicCalibratorModel,
    ThresholdPolicy,
)
from modelwork.domain.calibration.cold_start import ColdStartState
from modelwork.domain.calibration.decision_flow import process_extraction_for_decision
from modelwork.domain.calibration.novelty import (
    CentroidDistanceNoveltyModel,
    DeterministicNoveltyModel,
    NoveltyDetector,
    NoveltyFeatures,
    NoveltyPolicy,
    NoveltyResult,
    score_novelty,
    validate_work_envelope_novelty,
)
from modelwork.domain.stratum import stratum_key

from backend.domain.decision import route
from backend.models.entities import Extraction, OperationalAlert, ReviewTask


def _sample_work_envelope(
    calibrator_version: str = "calibrator_v1.0.0",
    novelty_detector_version: str = "novelty_v1.0.0",
    config_version: str = "cfg_2026_09",
) -> dict[str, Any]:
    return {
        "envelope_id": "11111111-1111-1111-1111-111111111111",
        "document_id": "22222222-2222-2222-2222-222222222222",
        "page_id": "33333333-3333-3333-3333-333333333333",
        "pinned_at": "2026-09-10T12:00:00Z",
        "model_versions": {
            "triage_classifier": "triage_v1",
            "printed_ocr": "ocr_v1",
            "hwr": "hwr_v1",
            "confidence_calibrator": calibrator_version,
            "novelty_detector": novelty_detector_version,
        },
        "config_version": config_version,
    }


def _sample_extraction(
    extraction_id: str = "ext-nov-001",
    field_name: str = "survey_number",
    token_conf: float = 0.92,
    raw_value: str = "120/A",
    engine: str = "printed_ocr",
) -> dict[str, Any]:
    return {
        "id": extraction_id,
        "page_id": "33333333-3333-3333-3333-333333333333",
        "field_name": field_name,
        "raw_value": raw_value,
        "engine": engine,
        "token_confidence": token_conf,
        "entry_status": "unknown",
        "model_version": "ocr_v1",
        "config_version": "cfg_2026_09",
    }


class _MockSession:
    def __init__(self) -> None:
        self.extractions: dict[str, Extraction] = {}
        self.alerts: list[OperationalAlert] = []
        self.added: list[Any] = []

    def get(self, entity_cls: Any, ident: Any) -> Any:
        if entity_cls is Extraction:
            return self.extractions.get(ident)
        for obj in self.added:
            if isinstance(obj, entity_cls) and getattr(obj, "id", None) == ident:
                return obj
        return None

    def add(self, obj: Any) -> None:
        if not hasattr(obj, "id") or obj.id is None:
            import uuid
            obj.id = str(uuid.uuid4())
        self.added.append(obj)
        if isinstance(obj, OperationalAlert):
            self.alerts.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def execute(self, query: Any) -> Any:
        class _Result:
            def __init__(self, alerts: list[OperationalAlert], query: Any) -> None:
                self._alerts = alerts
                self._query = query

            def scalar_one_or_none(self) -> Any:
                if not self._alerts:
                    return None
                return self._alerts[-1]

        return _Result(self.alerts, query)


def test_a_in_regime_input_evaluation():
    """An input embedding close to the known stratum prototype produces score <= threshold and in_regime."""
    canonical_stratum = stratum_key(
        field_class="survey_number",
        script="kannada",
        print_or_handwriting="printed",
        legibility_band="good",
        writer_cluster_id="default",
    )
    prototype_emb = [0.2, 0.4, 0.6, 0.8]
    prototypes = {canonical_stratum: prototype_emb}

    policy = NoveltyPolicy(novelty_threshold=0.50, reject_unseen_strata=True)
    detector = CentroidDistanceNoveltyModel(stratum_prototypes=prototypes, policy=policy)

    in_regime_emb = [0.21, 0.39, 0.61, 0.79]
    features = NoveltyFeatures(
        embedding=in_regime_emb,
        stratum=canonical_stratum,
        field_class="survey_number",
        script="kannada",
        print_or_handwriting="printed",
        legibility_band="good",
    )

    result = detector.evaluate_novelty(features)

    assert result.novelty_score <= policy.novelty_threshold
    assert result.is_outside_regime is False
    assert result.regime_reason == "in_regime"
    assert result.cluster_id is None
    assert result.model_version == "novelty_v1.0.0"


def test_b1_novel_input_embedding_distance_exceeded():
    """An input embedding distant from known stratum prototype produces score > threshold and outside_regime."""
    canonical_stratum = stratum_key(
        field_class="survey_number",
        script="kannada",
        print_or_handwriting="printed",
        legibility_band="good",
        writer_cluster_id="default",
    )
    prototype_emb = [0.1, 0.1, 0.1, 0.1]
    prototypes = {canonical_stratum: prototype_emb}

    policy = NoveltyPolicy(novelty_threshold=0.40, reject_unseen_strata=True)
    detector = CentroidDistanceNoveltyModel(stratum_prototypes=prototypes, policy=policy)

    novel_emb = [0.9, 0.9, 0.9, 0.9]
    features = NoveltyFeatures(
        embedding=novel_emb,
        stratum=canonical_stratum,
        field_class="survey_number",
        script="kannada",
        print_or_handwriting="printed",
        legibility_band="good",
    )

    result = detector.evaluate_novelty(features)

    assert result.novelty_score > policy.novelty_threshold
    assert result.is_outside_regime is True
    assert result.regime_reason == "embedding_distance_exceeded"
    assert result.cluster_id is not None
    assert result.cluster_id.startswith("novelty:stratum:")


def test_b2_unseen_stratum_outside_regime():
    """An input from an unseen stratum is immediately gated outside regime (FR-CNF-14)."""
    known_stratum = "survey_number|kannada|printed|good|default"
    prototypes = {known_stratum: [0.5, 0.5, 0.5, 0.5]}

    policy = NoveltyPolicy(novelty_threshold=0.50, reject_unseen_strata=True)
    detector = CentroidDistanceNoveltyModel(stratum_prototypes=prototypes, policy=policy)

    features = NoveltyFeatures(
        embedding=[0.5, 0.5, 0.5, 0.5],
        stratum="tax_amount|devanagari|handwritten|poor|cluster_99",
        field_class="tax_amount",
        script="devanagari",
        print_or_handwriting="handwritten",
        legibility_band="poor",
        writer_cluster_id="cluster_99",
    )

    result = detector.evaluate_novelty(features)

    assert result.novelty_score == 1.0
    assert result.is_outside_regime is True
    assert result.regime_reason == "unseen_stratum"
    assert result.cluster_id == "novelty:writer:cluster_99"


def test_c_determinism_with_identical_inputs():
    """Novelty scoring is strictly deterministic across repeated evaluations."""
    canonical_stratum = "parcel_no|devanagari|printed|medium|default"
    prototypes = {canonical_stratum: [0.3, 0.4, 0.5, 0.6]}
    detector = CentroidDistanceNoveltyModel(stratum_prototypes=prototypes)

    features = NoveltyFeatures(
        embedding=[0.35, 0.45, 0.55, 0.65],
        stratum=canonical_stratum,
    )

    first_result = detector.evaluate_novelty(features)
    for _ in range(10):
        next_result = detector.evaluate_novelty(features)
        assert next_result.novelty_score == first_result.novelty_score
        assert next_result.is_outside_regime == first_result.is_outside_regime
        assert next_result.regime_reason == first_result.regime_reason
        assert next_result.cluster_id == first_result.cluster_id

    score1 = score_novelty([0.35, 0.45, 0.55, 0.65], canonical_stratum, prototypes)
    score2 = score_novelty([0.35, 0.45, 0.55, 0.65], canonical_stratum, prototypes)
    assert score1 == score2


def test_d_confidence_independence_from_novelty():
    """Novelty scoring operates strictly independent of raw and calibrated confidence."""
    canonical_stratum = "owner_name|kannada|handwritten|medium|cluster_01"
    prototypes = {canonical_stratum: [0.1, 0.1, 0.1, 0.1]}
    policy = NoveltyPolicy(novelty_threshold=0.30)
    detector = CentroidDistanceNoveltyModel(stratum_prototypes=prototypes, policy=policy)

    novel_emb = [0.8, 0.8, 0.8, 0.8]
    features = NoveltyFeatures(
        embedding=novel_emb,
        stratum=canonical_stratum,
        field_class="owner_name",
        script="kannada",
        print_or_handwriting="handwritten",
        legibility_band="medium",
        writer_cluster_id="cluster_01",
    )

    assert not hasattr(features, "token_confidence")
    assert not hasattr(features, "calibrated_confidence")

    result = detector.evaluate_novelty(features)
    assert result.is_outside_regime is True

    envelope = _sample_work_envelope()
    calib = DeterministicCalibratorModel(lambda f: (0.98, {"token_confidence": 0.95}))
    thresh_policy = ThresholdPolicy(target_error_rate=0.05)

    ext_high_conf = _sample_extraction(token_conf=0.99)
    updated_ext, calib_res, _ = process_extraction_for_decision(
        extraction=ext_high_conf,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=thresh_policy,
        trace_id="doc:page",
        novelty_detector=detector,
        novelty_features=features,
    )

    assert updated_ext["routing_outcome"] == "outside_calibrated_regime"
    assert updated_ext["calibrated_confidence"] == 0.98
    assert updated_ext["novelty_score"] == result.novelty_score
    assert updated_ext["novelty_score"] > 0.30


def test_e_pinned_provenance_preservation():
    """Pinned novelty_detector and config_version from WorkEnvelope are verified and preserved."""
    envelope = _sample_work_envelope(
        novelty_detector_version="pinned_novelty_detector_v2.1",
        config_version="pinned_config_v99",
    )

    mod_ver, cfg_ver = validate_work_envelope_novelty(envelope)
    assert mod_ver == "pinned_novelty_detector_v2.1"
    assert cfg_ver == "pinned_config_v99"

    invalid_env = dict(envelope)
    invalid_env["model_versions"] = {"confidence_calibrator": "calib_v1"}
    with pytest.raises(ValueError, match="novelty_detector"):
        validate_work_envelope_novelty(invalid_env)

    invalid_env_cfg = dict(envelope)
    del invalid_env_cfg["config_version"]
    with pytest.raises(ValueError, match="config_version"):
        validate_work_envelope_novelty(invalid_env_cfg)


def test_f_exact_threshold_boundary_behavior():
    """Boundary conditions: score == threshold is in-regime; score > threshold is outside-regime."""
    policy = NoveltyPolicy(novelty_threshold=0.50)

    assert policy.is_outside_regime(0.50, stratum_is_known=True) is False
    assert policy.is_outside_regime(0.500001, stratum_is_known=True) is True
    assert policy.is_outside_regime(0.499999, stratum_is_known=True) is False

    strict_policy = NoveltyPolicy(novelty_threshold=0.20)
    assert strict_policy.is_outside_regime(0.25, stratum_is_known=True) is True

    permissive_policy = NoveltyPolicy(novelty_threshold=0.80)
    assert permissive_policy.is_outside_regime(0.75, stratum_is_known=True) is False


def test_g_invalid_inputs_and_edge_cases_rejected():
    """Invalid policies, embeddings, and non-finite numbers are rejected with clear errors."""
    with pytest.raises(ValueError, match="finite real number"):
        NoveltyPolicy(novelty_threshold=float("nan"))
    with pytest.raises(ValueError, match="finite real number"):
        NoveltyPolicy(novelty_threshold=float("inf"))
    with pytest.raises(ValueError):
        NoveltyPolicy(novelty_threshold=-0.05)
    with pytest.raises(ValueError):
        NoveltyPolicy(novelty_threshold=1.05)

    with pytest.raises(ValueError, match="non-empty list"):
        NoveltyFeatures(embedding=[])
    with pytest.raises(ValueError, match="finite real number"):
        NoveltyFeatures(embedding=[0.1, float("nan"), 0.3])
    with pytest.raises(ValueError, match="finite real number"):
        NoveltyFeatures(embedding=[0.1, float("inf"), 0.3])

    with pytest.raises(ValueError):
        NoveltyFeatures(layout_certainty=1.5)

    prototypes = {"stratum_a": [0.1, 0.2, 0.3, 0.4]}
    detector = CentroidDistanceNoveltyModel(stratum_prototypes=prototypes)
    mismatched_feats = NoveltyFeatures(embedding=[0.1, 0.2], stratum="stratum_a")
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        detector.evaluate_novelty(mismatched_feats)


def test_h_decision_flow_integration_novelty_gating():
    """Decision flow integrates novelty detection, gates the regime, and populates DecisionEnvelope."""
    canonical_stratum = "survey_number|kannada|printed|medium|default"
    prototypes = {canonical_stratum: [0.1, 0.1, 0.1, 0.1]}
    policy = NoveltyPolicy(novelty_threshold=0.40)
    detector = CentroidDistanceNoveltyModel(
        stratum_prototypes=prototypes,
        policy=policy,
        model_version="novelty_v1.0.0",
        config_version="cfg_2026_09",
    )

    calib = DeterministicCalibratorModel(lambda f: (0.95, {}))
    thresh_policy = ThresholdPolicy(target_error_rate=0.05)
    envelope = _sample_work_envelope()

    # Case 1: Out-of-regime input
    novel_ext = _sample_extraction(extraction_id="ext-novel-1", token_conf=0.95)
    novel_feats = NoveltyFeatures(
        embedding=[0.9, 0.9, 0.9, 0.9],
        stratum=canonical_stratum,
        field_class="survey_number",
        script="kannada",
        writer_cluster_id="cluster_07",
    )

    updated_ext, calib_res, pub_env = process_extraction_for_decision(
        extraction=novel_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=thresh_policy,
        trace_id="doc:page",
        novelty_detector=detector,
        novelty_features=novel_feats,
    )

    assert updated_ext["routing_outcome"] == "outside_calibrated_regime"
    assert updated_ext["novelty_score"] is not None
    assert updated_ext["novelty_score"] > 0.40
    assert pub_env is not None
    assert pub_env["novelty_cluster_id"] == "novelty:writer:cluster_07"
    assert pub_env["payload"]["novelty_score"] == updated_ext["novelty_score"]

    # Case 2: In-regime input with high confidence -> auto_accept
    in_regime_ext = _sample_extraction(extraction_id="ext-in-regime-1", token_conf=0.95)
    in_regime_feats = NoveltyFeatures(
        embedding=[0.11, 0.11, 0.11, 0.11],
        stratum=canonical_stratum,
        field_class="survey_number",
        script="kannada",
    )

    updated_in, _, pub_env_in = process_extraction_for_decision(
        extraction=in_regime_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=thresh_policy,
        trace_id="doc:page",
        novelty_detector=detector,
        novelty_features=in_regime_feats,
    )

    assert updated_in["routing_outcome"] == "auto_accept"
    assert updated_in["novelty_score"] <= 0.40
    assert pub_env_in.get("novelty_cluster_id") is None


def test_i_backend_decision_and_cluster_deduplication():
    """DecisionEnvelope with outside_calibrated_regime routes to OperationalAlert and deduplicates clusters."""
    session = _MockSession()

    cluster_handle = "novelty:doc:ror:kannada"

    ext1 = Extraction(
        id="ext-nov-01",
        routing_outcome="outside_calibrated_regime",
        calibrated_confidence=0.95,
    )
    session.extractions[ext1.id] = ext1

    res1 = route(session, ext1.id, novelty_cluster_id=cluster_handle)
    assert res1["outcome"] == "outside_calibrated_regime"
    assert res1["deduplicated"] is False
    assert res1["alert_id"] is not None
    assert len(session.alerts) == 1

    tasks = [obj for obj in session.added if isinstance(obj, ReviewTask)]
    assert len(tasks) == 0

    ext2 = Extraction(
        id="ext-nov-02",
        routing_outcome="outside_calibrated_regime",
        calibrated_confidence=0.92,
    )
    session.extractions[ext2.id] = ext2

    res2 = route(session, ext2.id, novelty_cluster_id=cluster_handle)
    assert res2["outcome"] == "outside_calibrated_regime"
    assert res2["deduplicated"] is True
    assert res2["alert_id"] == res1["alert_id"]

    assert len(session.alerts) == 1
    assert "ext-nov-01" in session.alerts[0].extraction_ids
    assert "ext-nov-02" in session.alerts[0].extraction_ids
