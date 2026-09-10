"""Integration and contract boundary tests between Model Work DecisionEnvelope and Backend Decision Engine.

Validates the full boundary flow:
WorkEnvelope -> Extraction -> Model Work Decision Flow -> DecisionEnvelope -> DECISION_QUEUE -> Backend Decision Engine.
Tests A through I verify the 5 authoritative routing destinations, cold-start postures, provenance preservation,
data integrity, failure rejection, determinism, and client-safety serialization.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
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
    def where(self, *a: Any, **kw: Any) -> MockQuery: return self
    def order_by(self, *a: Any, **kw: Any) -> MockQuery: return self
    def limit(self, *a: Any, **kw: Any) -> MockQuery: return self

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

# Import Model Work components
from modelwork.domain.calibration.calibrator import (
    DeterministicCalibratorModel,
    ThresholdPolicy,
)
from modelwork.domain.calibration.cold_start import ColdStartState
from modelwork.domain.calibration.decision_flow import process_extraction_for_decision

# Import Backend components
from backend.domain.decision import (
    VALID_ROUTING_OUTCOMES,
    UnroutableExtraction,
    route,
)
from backend.models.entities import (
    AuditSample,
    Conflict,
    Extraction,
    OperationalAlert,
    ReviewTask,
)
from backend.workers.decision_engine import handle


# In-memory mock database session for backend decision engine testing
class MockSession:
    """Lightweight in-memory SQLAlchemy Session mock for backend decision engine."""

    def __init__(self, extractions: list[Any] | None = None) -> None:
        self.extractions: dict[str, Any] = {e.id: e for e in (extractions or [])}
        self.added: list[Any] = []
        self.alerts: list[Any] = []

    def get(self, entity_cls: Any, ident: str) -> Any:
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
            def __init__(self, alerts: list[Any]) -> None:
                self._alerts = alerts

            def scalar_one_or_none(self) -> Any:
                return self._alerts[-1] if self._alerts else None

        return _Result(self.alerts)


class TestExtractionEntity:
    """Lightweight test extraction entity matching backend Extraction ORM attributes."""

    def __init__(
        self,
        extraction_id: str,
        page_id: str = "11111111-1111-1111-1111-111111111111",
        field_name: str = "survey_number",
        raw_value: str = "42/B",
        token_confidence: float = 0.92,
        calibrated_confidence: float | None = None,
        routing_outcome: str | None = None,
        entry_status: str = "unknown",
    ) -> None:
        self.id = extraction_id
        self.page_id = page_id
        self.field_name = field_name
        self.raw_value = raw_value
        self.token_confidence = token_confidence
        self.calibrated_confidence = calibrated_confidence
        self.routing_outcome = routing_outcome
        self.entry_status = entry_status


def _sample_work_envelope(
    calibrator_version: str = "calibrator_v1.0.0",
    config_version: str = "cfg_v2026_01",
    trace_id: str = "doc-uuid-001:page-uuid-001",
) -> dict[str, Any]:
    return {
        "envelope_id": "00000000-0000-0000-0000-000000000001",
        "document_id": "00000000-0000-0000-0000-000000000010",
        "page_id": "00000000-0000-0000-0000-000000000100",
        "pinned_at": "2026-09-10T12:00:00Z",
        "model_versions": {
            "triage_classifier": "triage_v1.0.0",
            "printed_ocr": "ocr_v1.0.0",
            "hwr": "hwr_v1.0.0",
            "confidence_calibrator": calibrator_version,
            "novelty_detector": "novelty_v1.0.0",
        },
        "config_version": config_version,
    }


def _sample_raw_extraction(
    extraction_id: str = "ext-boundary-001",
    token_confidence: float = 0.90,
    field_name: str = "owner_name",
) -> dict[str, Any]:
    return {
        "id": extraction_id,
        "page_id": "00000000-0000-0000-0000-000000000100",
        "field_name": field_name,
        "raw_value": "Sample Landholder",
        "engine": "printed_ocr",
        "model_version": "ocr_v1.0.0",
        "config_version": "cfg_v2026_01",
        "token_confidence": token_confidence,
        "entry_status": "unknown",
    }


# Test A — Auto Accept: High calibrated confidence + valid WorkEnvelope -> DecisionEnvelope -> Backend
def test_a_auto_accept_end_to_end():
    """Verify high calibrated confidence emits auto_accept and backend clears it without review task."""
    raw_ext = _sample_raw_extraction(extraction_id="ext-auto-01", token_confidence=0.92)
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)  # threshold 0.95
    calib = DeterministicCalibratorModel(lambda f: (0.98, {"token": 0.98}))
    trace_id = "trace-auto-01"

    updated_ext, calib_res, decision_msg = process_extraction_for_decision(
        extraction=raw_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id=trace_id,
        cold_start_state=ColdStartState.STEADY,
        publish_to_queue=True,
    )

    assert decision_msg is not None
    assert decision_msg["producer"] == "confidence-novelty"
    assert decision_msg["payload"]["routing_outcome"] == "auto_accept"

    # Backend consumes DecisionEnvelope
    db_ext = TestExtractionEntity(extraction_id="ext-auto-01")
    session = MockSession([db_ext])
    backend_res = handle(decision_msg, session)

    assert backend_res["outcome"] == "auto_accept"
    assert backend_res["extraction_id"] == "ext-auto-01"
    assert db_ext.routing_outcome == "auto_accept"
    assert db_ext.calibrated_confidence == 0.98
    # Invariant: auto_accept creates NO ReviewTask
    review_tasks = [x for x in session.added if isinstance(x, ReviewTask)]
    assert len(review_tasks) == 0


# Test B — Review: Below configured threshold
def test_b_review_end_to_end():
    """Verify below-threshold confidence emits review and backend creates routed review task."""
    raw_ext = _sample_raw_extraction(extraction_id="ext-review-01", token_confidence=0.75)
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)  # threshold 0.95
    calib = DeterministicCalibratorModel(lambda f: (0.88, {}))  # 0.88 < 0.95

    updated_ext, _, decision_msg = process_extraction_for_decision(
        extraction=raw_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="trace-review-01",
        publish_to_queue=True,
    )

    assert decision_msg is not None
    assert decision_msg["payload"]["routing_outcome"] == "review"

    db_ext = TestExtractionEntity(extraction_id="ext-review-01")
    session = MockSession([db_ext])
    backend_res = handle(decision_msg, session)

    assert backend_res["outcome"] == "review"
    assert db_ext.routing_outcome == "review"
    review_tasks = [x for x in session.added if isinstance(x, ReviewTask)]
    assert len(review_tasks) == 1
    assert review_tasks[0].source_stream == "routed"


# Test C — Audit: Configured audit candidate -> audit_sample
def test_c_audit_sample_end_to_end():
    """Verify audit candidate routes to audit_sample and backend creates indistinguishable review task and sample row."""
    raw_ext = _sample_raw_extraction(extraction_id="ext-audit-01", token_confidence=0.96)
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)
    calib = DeterministicCalibratorModel(lambda f: (0.97, {}))

    updated_ext, _, decision_msg = process_extraction_for_decision(
        extraction=raw_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="trace-audit-01",
        force_audit_sample=True,
        publish_to_queue=True,
    )

    assert decision_msg is not None
    assert decision_msg["payload"]["routing_outcome"] == "audit_sample"

    db_ext = TestExtractionEntity(extraction_id="ext-audit-01")
    session = MockSession([db_ext])
    backend_res = handle(decision_msg, session)

    assert backend_res["outcome"] == "audit_sample"
    assert db_ext.routing_outcome == "audit_sample"
    review_tasks = [x for x in session.added if isinstance(x, ReviewTask)]
    audit_samples = [x for x in session.added if isinstance(x, AuditSample)]
    assert len(review_tasks) == 1
    assert review_tasks[0].source_stream == "audit"
    assert len(audit_samples) == 1
    assert audit_samples[0].extraction_id == "ext-audit-01"
    assert audit_samples[0].model_confidence == 0.97


# Test D — Conflict: Existing conflict input -> conflict
def test_d_conflict_end_to_end():
    """Verify validator conflict input routes to conflict and backend creates conflict register entry."""
    raw_ext = _sample_raw_extraction(extraction_id="ext-cfl-01", token_confidence=0.99)
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)
    calib = DeterministicCalibratorModel(lambda f: (0.99, {}))

    updated_ext, _, decision_msg = process_extraction_for_decision(
        extraction=raw_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="trace-cfl-01",
        is_conflict=True,
        publish_to_queue=True,
    )

    assert decision_msg is not None
    assert decision_msg["payload"]["routing_outcome"] == "conflict"

    db_ext = TestExtractionEntity(extraction_id="ext-cfl-01")
    session = MockSession([db_ext])
    backend_res = handle(decision_msg, session)

    assert backend_res["outcome"] == "conflict"
    conflicts = [x for x in session.added if isinstance(x, Conflict)]
    assert len(conflicts) == 1
    assert conflicts[0].origin == "validator"
    assert "ext-cfl-01" in conflicts[0].records


# Test E — Outside Calibrated Regime: Upstream novelty signal -> outside_calibrated_regime
def test_e_outside_calibrated_regime_end_to_end():
    """Verify upstream novelty routes to outside_calibrated_regime and backend creates operational alert with 0 review tasks."""
    raw_ext = _sample_raw_extraction(extraction_id="ext-nov-01", token_confidence=0.99)
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)
    calib = DeterministicCalibratorModel(lambda f: (0.99, {}))

    updated_ext, _, decision_msg = process_extraction_for_decision(
        extraction=raw_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="trace-nov-01",
        is_outside_regime=True,
        publish_to_queue=True,
    )

    assert decision_msg is not None
    assert decision_msg["payload"]["routing_outcome"] == "outside_calibrated_regime"

    db_ext = TestExtractionEntity(extraction_id="ext-nov-01")
    session = MockSession([db_ext])
    backend_res = handle(decision_msg, session)

    assert backend_res["outcome"] == "outside_calibrated_regime"
    assert db_ext.routing_outcome == "outside_calibrated_regime"
    # Invariant: outside_calibrated_regime creates NO per-field review tasks
    review_tasks = [x for x in session.added if isinstance(x, ReviewTask)]
    assert len(review_tasks) == 0
    # Creates operational alert
    alerts = [x for x in session.added if isinstance(x, OperationalAlert)]
    assert len(alerts) == 1
    assert alerts[0].kind == "outside_calibrated_regime"


# Test F — Provenance: trace_id, confidence_calibrator version, config_version
def test_f_provenance_preservation_end_to_end():
    """Verify trace_id, pinned calibrator version, and config_version are strictly preserved end-to-end."""
    envelope = _sample_work_envelope(
        calibrator_version="pinned_calibrator_v2.5.0",
        config_version="pinned_cfg_hash_777",
    )
    raw_ext = _sample_raw_extraction(extraction_id="ext-prov-01")
    policy = ThresholdPolicy(target_error_rate=0.05)
    calib = DeterministicCalibratorModel(lambda f: (0.91, {}))
    trace_id = "trace-e2e-provenance-test"

    updated_ext, _, decision_msg = process_extraction_for_decision(
        extraction=raw_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id=trace_id,
        publish_to_queue=True,
    )

    assert decision_msg is not None
    assert decision_msg["trace_id"] == trace_id
    assert decision_msg["work_envelope"]["model_versions"]["confidence_calibrator"] == "pinned_calibrator_v2.5.0"
    assert decision_msg["work_envelope"]["config_version"] == "pinned_cfg_hash_777"
    assert updated_ext["model_version"] == "pinned_calibrator_v2.5.0"
    assert updated_ext["config_version"] == "pinned_cfg_hash_777"


# Test G — Data Integrity: raw confidence unchanged, calibrated confidence independent
def test_g_data_integrity_end_to_end():
    """Verify token_confidence (raw) is preserved unmutated while calibrated_confidence is distinct and bounded."""
    raw_ext = _sample_raw_extraction(extraction_id="ext-data-01", token_confidence=0.72)
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)
    calib = DeterministicCalibratorModel(lambda f: (0.965, {}))

    updated_ext, calib_res, decision_msg = process_extraction_for_decision(
        extraction=raw_ext,
        work_envelope=envelope,
        calibrator=calib,
        threshold_policy=policy,
        trace_id="trace-data-01",
        publish_to_queue=True,
    )

    # Raw confidence strictly unchanged
    assert updated_ext["token_confidence"] == 0.72
    assert decision_msg["payload"]["token_confidence"] == 0.72
    # Calibrated confidence produced independently
    assert updated_ext["calibrated_confidence"] == 0.965
    assert decision_msg["payload"]["calibrated_confidence"] == 0.965
    assert 0.0 <= updated_ext["calibrated_confidence"] <= 1.0


# Test H — Invalid Routing: unsupported routing value rejected by backend
def test_h_invalid_routing_rejected_by_backend():
    """Verify backend decision engine strictly rejects unsupported routing values with UnroutableExtraction."""
    db_ext = TestExtractionEntity(extraction_id="ext-inv-01", routing_outcome="invalid_outcome")
    session = MockSession([db_ext])

    # Direct route call with invalid outcome raises UnroutableExtraction
    with pytest.raises(UnroutableExtraction):
        route(session, "ext-inv-01")

    # Queue message carrying invalid outcome raises UnroutableExtraction
    invalid_msg = {
        "payload": {
            "id": "ext-inv-01",
            "routing_outcome": "unsupported_magic_route",
        }
    }
    with pytest.raises(UnroutableExtraction):
        handle(invalid_msg, session)


# Test I — Determinism: Identical inputs produce equivalent decision envelopes
def test_i_determinism_end_to_end():
    """Verify identical input + WorkEnvelope + model produces byte-for-byte equivalent envelopes."""
    raw_ext = _sample_raw_extraction(extraction_id="ext-det-01")
    envelope = _sample_work_envelope()
    policy = ThresholdPolicy(target_error_rate=0.05)
    calib = DeterministicCalibratorModel(lambda f: (0.93, {"feat": 0.93}))

    u1, c1, msg1 = process_extraction_for_decision(
        extraction=raw_ext, work_envelope=envelope, calibrator=calib, threshold_policy=policy, trace_id="t-fixed"
    )
    u2, c2, msg2 = process_extraction_for_decision(
        extraction=raw_ext, work_envelope=envelope, calibrator=calib, threshold_policy=policy, trace_id="t-fixed"
    )

    assert u1 == u2
    assert c1.calibrated_confidence == c2.calibrated_confidence
    assert msg1["payload"] == msg2["payload"]
    assert msg1["work_envelope"] == msg2["work_envelope"]
    assert msg1["producer"] == msg2["producer"]
    assert msg1["trace_id"] == msg2["trace_id"]


# Test J — Masking and Client Safety: public view never exposes source_stream or internal model debug info
def test_j_client_safety_and_masking():
    """Verify strip_review_task_internals structurally prevents exposure of source_stream and internal fields."""
    from backend.api.serializers import strip_review_task_internals

    task_audit = {"id": "tsk-01", "extraction_id": "ext-01", "reason": None, "source_stream": "audit"}
    task_routed = {"id": "tsk-02", "extraction_id": "ext-02", "reason": None, "source_stream": "routed"}

    view_audit = strip_review_task_internals(task_audit)
    view_routed = strip_review_task_internals(task_routed)

    # FR-REV-11: Structurally no source_stream field
    assert not hasattr(view_audit, "source_stream")
    assert not hasattr(view_routed, "source_stream")
    audit_dict = view_audit.model_dump() if hasattr(view_audit, "model_dump") else view_audit.dict()
    assert "source_stream" not in audit_dict
    assert "calibrated_confidence" not in audit_dict
    assert "routing_outcome" not in audit_dict


if __name__ == "__main__":
    test_a_auto_accept_end_to_end()
    test_b_review_end_to_end()
    test_c_audit_sample_end_to_end()
    test_d_conflict_end_to_end()
    test_e_outside_calibrated_regime_end_to_end()
    test_f_provenance_preservation_end_to_end()
    test_g_data_integrity_end_to_end()
    test_h_invalid_routing_rejected_by_backend()
    test_i_determinism_end_to_end()
    test_j_client_safety_and_masking()
    print("All 10 Model Work -> Backend boundary integration tests passed successfully.")
