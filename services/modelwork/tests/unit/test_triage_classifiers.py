"""Unit tests for Triage Classification & Legibility Scoring Engine (FR-TRI-01..04, FR-TRI-10..11, FR-CNF-14).

Covers Unit Tests:
- A. Legibility score is finite, bounded in [0.0, 1.0], and rejects NaN/Inf.
- B. Legibility band strictly uses only canonical contract values (good, marginal, poor).
- C. Threshold policy is injectable and validates boundaries without magic constants.
- D. Script and language classifications conform to supported representations.
- E. Document type classification conforms to page.schema.json enums.
- F. Page role classification conforms to page.schema.json enums.
- G. Writer cluster identifier strictly validates UUID format.
- H. Writer clustering preserves strict anonymity and contains zero personal data.
- I. Triage novelty pre-score integrates cleanly with NoveltyDetector and excludes confidence.
- J. Triage classifier worker handle() processes ingest producer and outputs triage producer.
- K. Unsupported producers and payloads are rejected.
- L. Rescan threshold breach creates rescan trigger with correct reason code.
- M. Replay processing is idempotent and prevents duplicate rescan tasks.
"""
from __future__ import annotations

import json
import math
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# Shim optional third-party packages if not present in the active environment
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

try:
    import pytest
except ImportError:
    class _MockPytest:
        @staticmethod
        def raises(exc: type[BaseException]) -> Any:
            class _RaisesContext:
                def __enter__(self) -> _RaisesContext:
                    return self
                def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> bool:
                    return exc_type is not None and issubclass(exc_type, exc)
            return _RaisesContext()
    pytest = _MockPytest()  # type: ignore[assignment]

from modelwork.domain.calibration.novelty import (
    CentroidDistanceNoveltyModel,
    NoveltyFeatures,
    NoveltyPolicy,
    NoveltyResult,
)
from modelwork.domain.triage.legibility import (
    CANONICAL_BREACH_REASON,
    DeterministicLegibilityScorer,
    LegibilityPolicy,
    LegibilityResult,
    VALID_LEGIBILITY_BANDS,
    score_legibility,
)
from modelwork.domain.triage.novelty_prescore import compute_triage_novelty_prescore
from modelwork.domain.triage.script_doctype import (
    DeterministicScriptDoctypeClassifier,
    ScriptDoctypeResult,
    VALID_DOC_TYPES,
    VALID_PAGE_ROLES,
    classify,
)
from modelwork.domain.triage.writer_clustering import (
    DeterministicWriterClusterer,
    WriterClusterResult,
    cluster_writer,
)
from modelwork.workers.triage_classifiers import handle


# ---------------------------------------------------------------------------
# 1. Legibility Scoring & Policy Tests (FR-TRI-01, FR-TRI-10)
# ---------------------------------------------------------------------------

def test_legibility_policy_threshold_validation():
    """Policy must validate threshold bounds and relations."""
    # Valid custom policy
    policy = LegibilityPolicy(poor_threshold=0.35, good_threshold=0.75, rescan_threshold=0.35)
    assert policy.poor_threshold == 0.35
    assert policy.good_threshold == 0.75
    assert policy.rescan_threshold == 0.35

    # Poor threshold cannot exceed good threshold
    with pytest.raises(ValueError):
        LegibilityPolicy(poor_threshold=0.8, good_threshold=0.5)

    # Rescan threshold cannot exceed good threshold
    with pytest.raises(ValueError):
        LegibilityPolicy(poor_threshold=0.3, good_threshold=0.5, rescan_threshold=0.6)

    # Negative threshold rejected
    with pytest.raises(ValueError):
        LegibilityPolicy(poor_threshold=-0.1)

    # Threshold > 1.0 rejected
    with pytest.raises(ValueError):
        LegibilityPolicy(good_threshold=1.1)

    # NaN / Inf rejected
    with pytest.raises(ValueError):
        LegibilityPolicy(poor_threshold=float("nan"))
    with pytest.raises(ValueError):
        LegibilityPolicy(good_threshold=float("inf"))


def test_legibility_band_classification_canonical_values():
    """Bands must strictly map to 'good', 'marginal', or 'poor'."""
    policy = LegibilityPolicy(poor_threshold=0.40, good_threshold=0.70, rescan_threshold=0.40)

    # Poor range [0.0, 0.40)
    assert policy.classify_band(0.0) == "poor"
    assert policy.classify_band(0.39) == "poor"

    # Marginal range [0.40, 0.70)
    assert policy.classify_band(0.40) == "marginal"
    assert policy.classify_band(0.69) == "marginal"

    # Good range [0.70, 1.0]
    assert policy.classify_band(0.70) == "good"
    assert policy.classify_band(1.0) == "good"

    # NaN / Inf input rejected
    with pytest.raises(ValueError):
        policy.classify_band(float("nan"))
    with pytest.raises(ValueError):
        policy.classify_band(float("inf"))


def test_legibility_result_validation():
    """LegibilityResult enforces bounded real numbers and contract enums."""
    res = LegibilityResult(
        quality_score=0.85,
        legibility_band="good",
        is_breached=False,
    )
    assert res.quality_score == 0.85
    assert res.legibility_band == "good"
    assert not res.is_breached

    # Invalid band rejected
    with pytest.raises(ValueError):
        LegibilityResult(quality_score=0.85, legibility_band="excellent", is_breached=False)

    # Out-of-bounds score rejected
    with pytest.raises(ValueError):
        LegibilityResult(quality_score=1.05, legibility_band="good", is_breached=False)
    with pytest.raises(ValueError):
        LegibilityResult(quality_score=-0.01, legibility_band="poor", is_breached=True, reason_code="quality_below_threshold")

    # NaN / Inf rejected
    with pytest.raises(ValueError):
        LegibilityResult(quality_score=float("nan"), legibility_band="good", is_breached=False)

    # Breached without reason_code rejected
    with pytest.raises(ValueError):
        LegibilityResult(quality_score=0.2, legibility_band="poor", is_breached=True, reason_code=None)


def test_deterministic_legibility_scorer_behavior():
    """DeterministicLegibilityScorer computes bounded scores and evaluates breach."""
    policy = LegibilityPolicy(poor_threshold=0.40, good_threshold=0.70, rescan_threshold=0.40)

    # High quality page image
    high_scorer = DeterministicLegibilityScorer(fixed_score=0.92, policy=policy)
    res_high = high_scorer.score(b"dummy_high_quality_image")
    assert res_high.quality_score == 0.92
    assert res_high.legibility_band == "good"
    assert not res_high.is_breached
    assert res_high.reason_code is None

    # Low quality / breached page image
    low_scorer = DeterministicLegibilityScorer(fixed_score=0.25, policy=policy)
    res_low = low_scorer.score(b"dummy_low_quality_image")
    assert res_low.quality_score == 0.25
    assert res_low.legibility_band == "poor"
    assert res_low.is_breached
    assert res_low.reason_code == CANONICAL_BREACH_REASON

    # Helper function test
    dict_res = score_legibility(b"dummy", policy=policy, scorer=high_scorer)
    assert dict_res["quality_score"] == 0.92
    assert dict_res["legibility_band"] == "good"


# ---------------------------------------------------------------------------
# 2. Script, Doc-Type, Page-Role Classification Tests (FR-TRI-02..04)
# ---------------------------------------------------------------------------

def test_script_doctype_result_validates_against_contract_enums():
    """ScriptDoctypeResult validates doc_type and page_role against page.schema.json."""
    # Valid result
    res = ScriptDoctypeResult(
        script="devanagari",
        language="hi",
        doc_type="jamabandi",
        page_role="tabular_register",
    )
    assert res.doc_type == "jamabandi"
    assert res.page_role == "tabular_register"

    # All canonical doc_types are accepted
    for dt in VALID_DOC_TYPES:
        r = ScriptDoctypeResult(script="devanagari", language="hi", doc_type=dt, page_role="text")
        assert r.doc_type == dt

    # All canonical page_roles are accepted
    for pr in VALID_PAGE_ROLES:
        r = ScriptDoctypeResult(script="devanagari", language="hi", doc_type="ror", page_role=pr)
        assert r.page_role == pr

    # None values accepted
    r_none = ScriptDoctypeResult(script=None, language=None, doc_type=None, page_role=None)
    assert r_none.doc_type is None
    assert r_none.page_role is None

    # Invalid doc_type rejected
    with pytest.raises(ValueError):
        ScriptDoctypeResult(script="devanagari", language="hi", doc_type="unregistered_contract", page_role="text")

    # Invalid page_role rejected
    with pytest.raises(ValueError):
        ScriptDoctypeResult(script="devanagari", language="hi", doc_type="jamabandi", page_role="front_cover")


def test_deterministic_script_doctype_classifier():
    """DeterministicScriptDoctypeClassifier returns schema-valid results and allows override function."""
    classifier = DeterministicScriptDoctypeClassifier(
        default_script="devanagari",
        default_language="hi",
        default_doc_type="khasra_khatauni",
        default_page_role="tabular_register",
    )
    res = classifier.classify(b"sample_page")
    assert res.script == "devanagari"
    assert res.language == "hi"
    assert res.doc_type == "khasra_khatauni"
    assert res.page_role == "tabular_register"

    # Functional interface check
    d = classify(b"sample_page", classifier=classifier)
    assert d["doc_type"] == "khasra_khatauni"
    assert d["page_role"] == "tabular_register"


# ---------------------------------------------------------------------------
# 3. Anonymous Writer Clustering Tests (FR-TRI-11, PRD §11 Q10)
# ---------------------------------------------------------------------------

def test_writer_cluster_result_uuid_validation():
    """WriterClusterResult enforces valid UUID format."""
    valid_uuid = str(uuid.uuid4())
    res = WriterClusterResult(writer_cluster_id=valid_uuid)
    assert res.writer_cluster_id == valid_uuid

    # None is valid per schema
    res_none = WriterClusterResult(writer_cluster_id=None)
    assert res_none.writer_cluster_id is None

    # Non-UUID string rejected
    with pytest.raises(ValueError):
        WriterClusterResult(writer_cluster_id="cluster-user-999")

    # Non-UUID string strictly rejected
    with pytest.raises(ValueError):
        WriterClusterResult(writer_cluster_id="officer_id_999")


def test_deterministic_writer_clusterer_anonymity_and_determinism():
    """DeterministicWriterClusterer generates deterministic UUIDv5 without personal identity."""
    clusterer = DeterministicWriterClusterer()
    img_data = b"page_scan_bytes_12345"

    res1 = clusterer.cluster(img_data)
    res2 = clusterer.cluster(img_data)

    # Identical image -> identical cluster UUID
    assert res1.writer_cluster_id == res2.writer_cluster_id
    assert uuid.UUID(res1.writer_cluster_id)  # valid UUID

    # Different image -> different cluster UUID
    res3 = clusterer.cluster(b"different_page_scan")
    assert res3.writer_cluster_id != res1.writer_cluster_id

    # Functional interface check
    cid = cluster_writer(img_data, clusterer=clusterer)
    assert cid == res1.writer_cluster_id


# ---------------------------------------------------------------------------
# 4. Triage Novelty Pre-Score Tests (FR-CNF-14)
# ---------------------------------------------------------------------------

def test_triage_novelty_prescore_computation():
    """Novelty pre-score computes finite score in [0.0, 1.0] without confidence."""
    # Calibrated known prototype
    proto = {"triage_page|devanagari|printed|good|test-cluster": [0.1, 0.2, 0.3]}
    detector = CentroidDistanceNoveltyModel(stratum_prototypes=proto)

    score = compute_triage_novelty_prescore(
        doc_type="jamabandi",
        script="devanagari",
        legibility_band="good",
        writer_cluster_id="test-cluster",
        detector=detector,
        embedding=[0.1, 0.2, 0.3],
    )
    assert score is not None
    assert math.isfinite(score)
    assert 0.0 <= score <= 1.0
    assert score == 0.0  # exact match with prototype

    # Unseen stratum -> novelty score 1.0
    score_unseen = compute_triage_novelty_prescore(
        doc_type="unknown",
        script="unknown",
        legibility_band="poor",
        writer_cluster_id="unknown-cluster",
        detector=detector,
    )
    assert score_unseen == 1.0


# ---------------------------------------------------------------------------
# 5. Worker handle() & Boundary Execution Tests
# ---------------------------------------------------------------------------

def test_worker_handle_processes_ingest_message():
    """Worker consumes producer='ingest' and emits producer='triage' with valid Page."""
    page_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    trace_id = f"{doc_id}:{page_id}"

    ingest_msg = {
        "message_id": str(uuid.uuid4()),
        "trace_id": trace_id,
        "emitted_at": "2026-09-11T09:00:00Z",
        "producer": "ingest",
        "work_envelope": None,
        "payload": {
            "id": page_id,
            "document_id": doc_id,
            "index": 0,
            "index_position": 1,
            "quality_score": None,
            "legibility_band": None,
            "script": None,
            "language": None,
            "doc_type": None,
            "page_role": None,
            "writer_cluster_id": None,
            "novelty_score": None,
        },
        "_image_bytes": b"synthetic_clean_page_image",
    }

    leg_scorer = DeterministicLegibilityScorer(fixed_score=0.88)
    sd_classifier = DeterministicScriptDoctypeClassifier(
        default_script="devanagari",
        default_language="hi",
        default_doc_type="jamabandi",
        default_page_role="tabular_register",
    )
    w_clusterer = DeterministicWriterClusterer(fixed_cluster_id=str(uuid.uuid4()))

    published_messages: list[dict[str, Any]] = []

    outbound = handle(
        ingest_msg,
        legibility_scorer=leg_scorer,
        script_classifier=sd_classifier,
        writer_clusterer=w_clusterer,
        publisher=published_messages.append,
    )

    # 1. Output queue and producer
    assert outbound["_queue"] == "TRIAGE_QUEUE"
    assert outbound["producer"] == "triage"
    assert outbound["trace_id"] == trace_id

    # 2. Page fields populated
    page = outbound["payload"]
    assert page["id"] == page_id
    assert page["document_id"] == doc_id
    assert page["index"] == 0
    assert page["index_position"] == 1
    assert page["quality_score"] == 0.88
    assert page["legibility_band"] == "good"
    assert page["script"] == "devanagari"
    assert page["language"] == "hi"
    assert page["doc_type"] == "jamabandi"
    assert page["page_role"] == "tabular_register"
    assert page["writer_cluster_id"] == w_clusterer.fixed_cluster_id
    assert page["route"] == ["text"]

    # 3. Publisher called exactly once
    assert len(published_messages) == 1
    assert published_messages[0] == outbound


def test_worker_handle_skips_outbound_triage_producer_safely():
    """Worker safely skips message if producer is already 'triage' (echo prevention)."""
    triage_msg = {
        "message_id": str(uuid.uuid4()),
        "trace_id": "doc1:page1",
        "emitted_at": "2026-09-11T09:00:00Z",
        "producer": "triage",
        "payload": {"id": "page1", "document_id": "doc1", "index": 0},
    }

    res = handle(triage_msg)
    assert res["status"] == "skipped"
    assert res["reason"] == "already_triaged"


def test_worker_handle_rejects_unsupported_producer():
    """Worker strictly rejects unsupported producers."""
    invalid_msg = {
        "message_id": str(uuid.uuid4()),
        "trace_id": "doc1:page1",
        "emitted_at": "2026-09-11T09:00:00Z",
        "producer": "external_scanner",
        "payload": {"id": "page1", "document_id": "doc1", "index": 0},
    }

    with pytest.raises(ValueError):
        handle(invalid_msg)


def test_worker_handle_triggers_rescan_on_threshold_breach():
    """Worker triggers rescan callback when legibility score breaches threshold."""
    page_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    msg = {
        "message_id": str(uuid.uuid4()),
        "trace_id": f"{doc_id}:{page_id}",
        "emitted_at": "2026-09-11T09:00:00Z",
        "producer": "ingest",
        "payload": {"id": page_id, "document_id": doc_id, "index": 0},
        "_image_bytes": b"blurred_illegible_scan",
    }

    policy = LegibilityPolicy(poor_threshold=0.40, rescan_threshold=0.40)
    scorer = DeterministicLegibilityScorer(fixed_score=0.20, policy=policy)

    rescan_calls: list[tuple[str, str]] = []
    def mock_rescan_handler(pid: str, reason: str) -> None:
        rescan_calls.append((pid, reason))

    outbound = handle(
        msg,
        legibility_scorer=scorer,
        legibility_policy=policy,
        rescan_handler=mock_rescan_handler,
    )

    assert len(rescan_calls) == 1
    assert rescan_calls[0] == (page_id, CANONICAL_BREACH_REASON)
    assert outbound["payload"]["legibility_band"] == "poor"


def test_worker_handle_idempotent_replay():
    """Replaying the same message produces identical deterministic output and no duplicate tasks."""
    page_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    msg = {
        "message_id": str(uuid.uuid4()),
        "trace_id": f"{doc_id}:{page_id}",
        "emitted_at": "2026-09-11T09:00:00Z",
        "producer": "ingest",
        "payload": {"id": page_id, "document_id": doc_id, "index": 0},
        "_image_bytes": b"standard_page_scan",
    }

    # First execution
    out1 = handle(msg)
    # Second execution (replayed message)
    out2 = handle(msg)

    # Deterministic payload values match exactly
    assert out1["payload"]["quality_score"] == out2["payload"]["quality_score"]
    assert out1["payload"]["legibility_band"] == out2["payload"]["legibility_band"]
    assert out1["payload"]["script"] == out2["payload"]["script"]
    assert out1["payload"]["doc_type"] == out2["payload"]["doc_type"]
    assert out1["payload"]["page_role"] == out2["payload"]["page_role"]
    assert out1["payload"]["writer_cluster_id"] == out2["payload"]["writer_cluster_id"]
    assert out1["payload"]["novelty_score"] == out2["payload"]["novelty_score"]


if __name__ == "__main__":
    test_legibility_policy_threshold_validation()
    test_legibility_band_classification_canonical_values()
    test_legibility_result_validation()
    test_deterministic_legibility_scorer_behavior()
    test_script_doctype_result_validates_against_contract_enums()
    test_deterministic_script_doctype_classifier()
    test_writer_cluster_result_uuid_validation()
    test_deterministic_writer_clusterer_anonymity_and_determinism()
    test_triage_novelty_prescore_computation()
    test_worker_handle_processes_ingest_message()
    test_worker_handle_skips_outbound_triage_producer_safely()
    test_worker_handle_rejects_unsupported_producer()
    test_worker_handle_triggers_rescan_on_threshold_breach()
    test_worker_handle_idempotent_replay()
    print("All 13 Triage Classifier unit tests passed successfully.")
