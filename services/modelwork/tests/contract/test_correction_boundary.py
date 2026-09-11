"""Contract and unit tests for P0 Correction -> Attribution -> Provenance -> LEARNING_LOOP_QUEUE boundary.

Verifies Tests A through M:
- Test A: Valid reviewer correction produces valid learning example and CorrectionLabel.
- Test B: Original prediction and corrected value preserved exactly (including vernacular/whitespace).
- Test C: Original model_version preserved without registry override.
- Test D: Original config_version preserved.
- Test E: source_page_digest preserved and passed to CorrectionLabel for leakage guards.
- Test F: Correct source_stream preserved (routed, audit, downstream, legacy_digital) and invalid rejected.
- Test G: edit_distance computed and preserved correctly.
- Test H: reliability_weight preserved for legacy_digital stream, rejected if negative/non-finite.
- Test I: Feature attribution output is strictly deterministic, stably ordered, and excludes novelty.
- Test J: Officer-facing ReviewTaskPublicView retains zero source_stream leakage (FR-REV-11).
- Test K: Queue payload and envelope validate against contracts/schemas/correction.schema.json and asyncapi.
- Test L: Missing required provenance (model_version, config_version, source_page_digest) rejected.
- Test M: Maker-checker pending edits do not emit before confirmation; duplicate/self-confirmation rejected.
"""
from __future__ import annotations

import json
import math
import sys
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

# Shim optional third-party packages if not present in the active environment
def _make_mock_pkg(name: str) -> MagicMock:
    m = MagicMock()
    m.__path__ = []
    m.__file__ = f"{name}/__init__.py"
    return m

import types

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

if "sqlalchemy.exc" not in sys.modules:
    _exc_mod = types.ModuleType("sqlalchemy.exc")
    class IntegrityError(Exception): pass
    _exc_mod.IntegrityError = IntegrityError
    sys.modules["sqlalchemy.exc"] = _exc_mod
    sys.modules["sqlalchemy"].exc = _exc_mod

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
from modelwork.domain.calibration.attribution import (
    FeatureAttributionItem,
    format_attribution_reason,
    format_feature_attributions,
)
from modelwork.domain.calibration.calibrator import (
    CalibratorFeatures,
    CorrectionLabel,
)
from modelwork.domain.learning_loop.collector import (
    LearningExample,
    collect_learning_example,
    learning_example_to_correction_label,
)

# Import Backend entities and workflow components
from backend.domain.correction import (
    PendingCorrectionAlreadyResolved,
    SameActorCannotConfirm,
    confirm_pending_correction,
    edit_distance,
    submit_correction,
)
from backend.models.entities import (
    Correction,
    Extraction,
    Page,
    PendingCorrection,
    ReviewTask,
)
from backend.publishers.learning_loop_publisher import (
    QUEUE_NAME,
    build_learning_loop_envelope,
    publish_correction_to_outbox,
)
from landoutbox.models import OutboxMessage


class _MockSession:
    """Lightweight in-memory SQLAlchemy Session mock for boundary testing."""

    def __init__(self) -> None:
        self.pages: dict[str, Page] = {}
        self.extractions: dict[str, Extraction] = {}
        self.pending: dict[str, PendingCorrection] = {}
        self.corrections: dict[str, Correction] = {}
        self.added: list[Any] = []

    def get(self, entity_cls: Any, ident: str) -> Any:
        if entity_cls is Page:
            return self.pages.get(ident)
        if entity_cls is Extraction:
            return self.extractions.get(ident)
        if entity_cls is PendingCorrection:
            return self.pending.get(ident)
        if entity_cls is Correction:
            return self.corrections.get(ident)
        for obj in self.added:
            if isinstance(obj, entity_cls) and getattr(obj, "id", None) == ident:
                return obj
        return None

    def add(self, obj: Any) -> None:
        curr_id = getattr(obj, "id", None)
        if curr_id is None or isinstance(curr_id, MockColumn) or not isinstance(curr_id, str):
            obj.id = str(uuid.uuid4())
        curr_state = getattr(obj, "state", None)
        if curr_state is None or isinstance(curr_state, MockColumn):
            obj.state = "pending"
        self.added.append(obj)
        if isinstance(obj, PendingCorrection):
            self.pending[obj.id] = obj
        elif isinstance(obj, Correction):
            self.corrections[obj.id] = obj

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def execute(self, query: Any) -> Any:
        class _Result:
            def scalars(self) -> Any:
                return self
            def scalar_one_or_none(self) -> Any:
                return None
            def first(self) -> Any:
                return None
            def all(self) -> list[Any]:
                return []
        return _Result()



def test_a_valid_reviewer_correction_produces_valid_learning_example():
    """Test A: Valid reviewer correction produces valid learning example and CorrectionLabel."""
    corr_payload = {
        "id": "11111111-1111-1111-1111-111111111111",
        "extraction_id": "22222222-2222-2222-2222-222222222222",
        "crop_uri": "/pages/p-01/crop?bbox=[10,10,50,50]",
        "predicted": "Rajesh",
        "corrected": "Ramesh",
        "edit_distance": 1,
        "actor": "officer-alpha",
        "model_version": "hwr_v1",
        "config_version": "cfg_v1",
        "stream": "routed",
        "source_page_digest": "sha256/ab/cd/digest123",
        "reliability_weight": None,
    }
    envelope = {
        "message_id": "33333333-3333-3333-3333-333333333333",
        "trace_id": "doc-01:page-01",
        "emitted_at": "2026-09-11T00:00:00Z",
        "producer": "review",
        "work_envelope": {
            "envelope_id": "44444444-4444-4444-4444-444444444444",
            "document_id": "55555555-5555-5555-5555-555555555555",
            "page_id": "66666666-6666-6666-6666-666666666666",
            "pinned_at": "2026-09-11T00:00:00Z",
            "model_versions": {
                "triage_classifier": "v1",
                "printed_ocr": "v1",
                "hwr": "v1",
                "confidence_calibrator": "v1",
                "novelty_detector": "v1",
            },
            "config_version": "cfg_v1",
        },
        "payload": corr_payload,
    }

    example = collect_learning_example(envelope)
    assert isinstance(example, LearningExample)
    assert example.correction_id == corr_payload["id"]
    assert example.extraction_id == corr_payload["extraction_id"]
    assert example.crop_uri == corr_payload["crop_uri"]
    assert example.predicted == "Rajesh"
    assert example.corrected == "Ramesh"
    assert example.edit_distance == 1
    assert example.is_correct is False
    assert example.actor == "officer-alpha"
    assert example.model_version == "hwr_v1"
    assert example.config_version == "cfg_v1"
    assert example.source_stream == "routed"
    assert example.source_page_digest == "sha256/ab/cd/digest123"
    assert example.trace_id == "doc-01:page-01"

    # Conversion to CorrectionLabel with stratum and leakage guard preserved
    features = CalibratorFeatures(
        token_confidence=0.82,
        field_class="owner_name",
        script="kannada",
        doc_type="ror",
        print_or_handwriting="handwritten",
    )
    label = learning_example_to_correction_label(example, features)
    assert isinstance(label, CorrectionLabel)
    assert label.extraction_id == example.extraction_id
    assert label.is_correct is False
    assert label.source_page_digest == example.source_page_digest
    assert label.stratum == features.canonical_stratum()


def test_b_original_prediction_and_corrected_value_preserved_exactly():
    """Test B: Original prediction and corrected value preserved exactly without mutation."""
    cases = [
        ("ರಾಮೇಶ್ ಕುಮಾರ್", "ರಾಮೇಶ್ವರ್ ಕುಮಾರ್", 2, False),
        ("राजाराम पाटिल", "राजाराम पाटिल", 0, True),
        ("  Survey No. 42/1-B  ", "Survey No. 42/1-B", 4, False),
        ("123/45", "123/45", 0, True),
        ("", "New Field Entry", 15, False),
    ]
    for pred, corr, dist, expected_correct in cases:
        raw = {
            "id": str(uuid.uuid4()),
            "extraction_id": str(uuid.uuid4()),
            "crop_uri": "/pages/p1/crop",
            "predicted": pred,
            "corrected": corr,
            "edit_distance": dist,
            "actor": "officer-01",
            "model_version": "m1",
            "config_version": "c1",
            "stream": "audit",
            "source_page_digest": "digest-xyz",
        }
        ex = collect_learning_example(raw)
        assert ex.predicted == pred
        assert ex.corrected == corr
        assert ex.edit_distance == dist
        assert ex.is_correct is expected_correct


def test_c_original_model_version_preserved():
    """Test C: Original model_version preserved without registry query override."""
    historical_versions = [
        "printed_ocr_v1.0.0",
        "hwr_legacy_v0.8_202511",
        "triage_snapshot_v2",
    ]
    for ver in historical_versions:
        raw = {
            "id": str(uuid.uuid4()),
            "extraction_id": str(uuid.uuid4()),
            "crop_uri": "/pages/p1/crop",
            "predicted": "A",
            "corrected": "B",
            "actor": "officer-01",
            "model_version": ver,
            "config_version": "cfg_v1",
            "stream": "routed",
            "source_page_digest": "digest-1",
        }
        ex = collect_learning_example(raw)
        assert ex.model_version == ver


def test_d_original_config_version_preserved():
    """Test D: Original config_version preserved."""
    configs = ["cfg_2026_01", "cfg_pinned_v4", "prod_hotfix_12"]
    for cfg in configs:
        raw = {
            "id": str(uuid.uuid4()),
            "extraction_id": str(uuid.uuid4()),
            "crop_uri": "/pages/p1/crop",
            "predicted": "A",
            "corrected": "B",
            "actor": "officer-01",
            "model_version": "m1",
            "config_version": cfg,
            "stream": "routed",
            "source_page_digest": "digest-1",
        }
        ex = collect_learning_example(raw)
        assert ex.config_version == cfg


def test_e_source_page_digest_preserved():
    """Test E: source_page_digest preserved and passed to CorrectionLabel for leakage isolation."""
    digests = [
        "sha256/a1/b2/e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "sha256/fe/dc/0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    ]
    for dig in digests:
        raw = {
            "id": str(uuid.uuid4()),
            "extraction_id": str(uuid.uuid4()),
            "crop_uri": "/pages/p1/crop",
            "predicted": "A",
            "corrected": "B",
            "actor": "officer-01",
            "model_version": "m1",
            "config_version": "c1",
            "stream": "routed",
            "source_page_digest": dig,
        }
        ex = collect_learning_example(raw)
        assert ex.source_page_digest == dig
        feat = CalibratorFeatures(token_confidence=0.88, field_class="survey_number")
        label = learning_example_to_correction_label(ex, feat)
        assert label.source_page_digest == dig


def test_f_correct_source_stream_preserved():
    """Test F: Correct source_stream preserved (routed, audit, downstream, legacy_digital)."""
    valid_streams = ["routed", "audit", "downstream", "legacy_digital"]
    for s in valid_streams:
        raw = {
            "id": str(uuid.uuid4()),
            "extraction_id": str(uuid.uuid4()),
            "crop_uri": "/pages/p1/crop",
            "predicted": "A",
            "corrected": "B",
            "actor": "officer-01",
            "model_version": "m1",
            "config_version": "c1",
            "stream": s,
            "source_page_digest": "dig-123",
        }
        ex = collect_learning_example(raw)
        assert ex.source_stream == s

    # Invalid stream rejected
    with pytest.raises(ValueError):
        collect_learning_example(dict(raw, stream="unverified_pool"))


def test_g_edit_distance_computed_and_preserved():
    """Test G: edit_distance computed and preserved correctly."""
    # Explicit edit_distance
    raw = {
        "id": str(uuid.uuid4()),
        "extraction_id": str(uuid.uuid4()),
        "crop_uri": "/pages/p1/crop",
        "predicted": "Ramesh",
        "corrected": "Rajesh",
        "edit_distance": 1,
        "actor": "officer-01",
        "model_version": "m1",
        "config_version": "c1",
        "stream": "routed",
        "source_page_digest": "dig-123",
    }
    ex = collect_learning_example(raw)
    assert ex.edit_distance == 1

    # Omitted edit_distance computed via Levenshtein fallback
    raw_omitted = dict(raw)
    del raw_omitted["edit_distance"]
    ex2 = collect_learning_example(raw_omitted)
    assert ex2.edit_distance == 1

    # Direct edit_distance check
    assert edit_distance("", "") == 0
    assert edit_distance("Ramesh", "Ramesh") == 0
    assert edit_distance("Ramesh", "Rajesh") == 1
    assert edit_distance("", "abc") == 3

    # Negative edit_distance rejected
    with pytest.raises(ValueError):
        collect_learning_example(dict(raw, edit_distance=-1))


def test_h_reliability_weight_preserved_for_legacy_digital():
    """Test H: reliability_weight preserved for legacy_digital, rejected if invalid."""
    raw = {
        "id": str(uuid.uuid4()),
        "extraction_id": str(uuid.uuid4()),
        "crop_uri": "/pages/p1/crop",
        "predicted": "Old Text",
        "corrected": "Correct Text",
        "actor": "migration_job",
        "model_version": "legacy_v0",
        "config_version": "cfg_legacy",
        "stream": "legacy_digital",
        "source_page_digest": "dig-legacy",
        "reliability_weight": 0.70,
    }
    ex = collect_learning_example(raw)
    assert ex.reliability_weight == 0.70

    # Negative weight rejected
    with pytest.raises(ValueError):
        collect_learning_example(dict(raw, reliability_weight=-0.1))

    # NaN weight rejected
    with pytest.raises(ValueError):
        collect_learning_example(dict(raw, reliability_weight=float("nan")))


def test_i_feature_attribution_output_is_deterministic_and_stably_ordered():
    """Test I: Feature attribution output is strictly deterministic, stably ordered, and excludes novelty."""
    features = CalibratorFeatures(
        token_confidence=0.75,
        layout_certainty=0.85,
        print_or_handwriting="handwritten",
        field_class="survey_number",
        script="kannada",
        doc_type="ror",
        validator_outcomes={"rule_a": "fail"},
    )
    # Attribution weights with magnitude ties
    attributions = {
        "token_confidence": -0.40,
        "layout_certainty": 0.40,
        "is_handwritten": -0.80,
        "validator_has_failure": 0.60,
    }

    items = format_feature_attributions(features, attributions)
    assert len(items) == 4

    # Largest absolute magnitude first: |is_handwritten| = 0.80
    assert items[0].feature_name == "is_handwritten"
    assert items[0].contribution == -0.80
    assert items[0].feature_value == 1.0

    # Second: |validator_has_failure| = 0.60
    assert items[1].feature_name == "validator_has_failure"
    assert items[1].contribution == 0.60
    assert items[1].feature_value is True

    # Third and Fourth: |layout_certainty| == |token_confidence| == 0.40.
    # Tie broken alphabetically: layout_certainty before token_confidence
    assert items[2].feature_name == "layout_certainty"
    assert items[2].contribution == 0.40
    assert items[2].feature_value == 0.85

    assert items[3].feature_name == "token_confidence"
    assert items[3].contribution == -0.40
    assert items[3].feature_value == 0.75

    # Determinism across permutations
    import random
    keys = list(attributions.keys())
    for _ in range(25):
        random.shuffle(keys)
        shuffled_dict = {k: attributions[k] for k in keys}
        res = format_feature_attributions(features, shuffled_dict)
        assert [x.feature_name for x in res] == [
            "is_handwritten", "validator_has_failure", "layout_certainty", "token_confidence"
        ]

    # ReviewTask.reason string generation (FR-REV-03/15)
    reason = format_attribution_reason(items, max_features=3)
    assert reason == "calibrator: is_handwritten (-0.80), validator_has_failure (+0.60), layout_certainty (+0.40)"


def test_j_officer_facing_review_task_public_view_retains_zero_source_stream_leakage():
    """Test J: Officer-facing ReviewTaskPublicView retains zero source_stream leakage (FR-REV-11)."""
    from backend.api.serializers import ReviewTaskPublicView, strip_review_task_internals

    class _TaskLike:
        def __init__(self) -> None:
            self.id = "task-uuid-1"
            self.extraction_id = "ext-uuid-1"
            self.reason = "calibrator: low confidence"
            self.assignee = "officer-01"
            self.opened_at = datetime.now(timezone.utc)
            self.closed_at = None
            self.cluster_id = None
            self.cluster_size = None
            self.hour_into_session = 0
            self.source_stream = "audit"  # Sensitive internal stream

    task = _TaskLike()
    view = strip_review_task_internals(task)
    assert isinstance(view, ReviewTaskPublicView)

    # Structurally absent from attributes and model dump
    assert not hasattr(view, "source_stream")
    dumped = view.model_dump()
    assert "source_stream" not in dumped
    assert "audit" not in dumped.values()


def test_k_queue_payload_validates_against_contracts():
    """Test K: Queue payload validates against contracts/schemas/correction.schema.json and asyncapi."""
    import jsonschema

    with open("contracts/schemas/correction.schema.json", "r", encoding="utf-8") as f:
        corr_schema = json.load(f)
    with open("contracts/schemas/work_envelope.schema.json", "r", encoding="utf-8") as f:
        work_env_schema = json.load(f)

    corr = Correction(
        id=str(uuid.uuid4()),
        extraction_id=str(uuid.uuid4()),
        crop_uri="/pages/p1/crop?bbox=[10,10,20,20]",
        predicted="42/A",
        corrected="42/B",
        edit_distance=1,
        actor="officer-01",
        model_version="ocr_v1",
        config_version="cfg_v1",
        stream="routed",
        source_page_digest="sha256/a1/b2/digest",
        reliability_weight=None,
    )
    payload = corr.to_contract_dict()

    # Must validate cleanly against contracts/schemas/correction.schema.json
    jsonschema.validate(instance=payload, schema=corr_schema)

    # Rejects forbidden extra keys (additionalProperties: false)
    bad_payload = dict(payload, extra_key="disallowed")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad_payload, schema=corr_schema)

    # Work envelope validation
    work_env = {
        "envelope_id": str(uuid.uuid4()),
        "document_id": str(uuid.uuid4()),
        "page_id": str(uuid.uuid4()),
        "pinned_at": datetime.now(timezone.utc).isoformat(),
        "model_versions": {
            "triage_classifier": "v1",
            "printed_ocr": "v1",
            "hwr": "v1",
            "confidence_calibrator": "v1",
            "novelty_detector": "v1",
        },
        "config_version": "cfg_v1",
    }
    jsonschema.validate(instance=work_env, schema=work_env_schema)

    # Full LearningLoopEnvelope construction
    envelope = build_learning_loop_envelope(
        correction_payload=payload,
        work_envelope=work_env,
        trace_id="doc:page",
        producer="review",
    )
    assert envelope["_queue"] == "LEARNING_LOOP_QUEUE"
    assert envelope["producer"] == "review"
    assert envelope["trace_id"] == "doc:page"
    assert envelope["payload"] == payload
    assert envelope["work_envelope"] == work_env


def test_l_missing_required_provenance_rejected_with_clear_errors():
    """Test L: Missing required provenance rejected with clear errors."""
    valid_base = {
        "id": str(uuid.uuid4()),
        "extraction_id": str(uuid.uuid4()),
        "crop_uri": "/pages/p1/crop",
        "predicted": "A",
        "corrected": "B",
        "actor": "officer-01",
        "model_version": "m1",
        "config_version": "c1",
        "stream": "routed",
        "source_page_digest": "dig-123",
    }

    # Missing model_version
    with pytest.raises(ValueError):
        collect_learning_example(dict(valid_base, model_version=""))
    with pytest.raises(ValueError):
        bad = dict(valid_base); del bad["model_version"]; collect_learning_example(bad)

    # Missing config_version
    with pytest.raises(ValueError):
        collect_learning_example(dict(valid_base, config_version=""))
    with pytest.raises(ValueError):
        bad = dict(valid_base); del bad["config_version"]; collect_learning_example(bad)

    # Missing source_page_digest
    with pytest.raises(ValueError):
        collect_learning_example(dict(valid_base, source_page_digest=""))
    with pytest.raises(ValueError):
        bad = dict(valid_base); del bad["source_page_digest"]; collect_learning_example(bad)

    # Missing actor
    with pytest.raises(ValueError):
        collect_learning_example(dict(valid_base, actor=""))

    # Missing extraction_id
    with pytest.raises(ValueError):
        bad = dict(valid_base); del bad["extraction_id"]; collect_learning_example(bad)


def test_m_maker_checker_pending_edits_do_not_emit_before_confirmation():
    """Test M: Maker-checker pending edits do not emit before confirmation; duplicate/self-confirmation rejected."""
    session = _MockSession()
    page = Page(id="page-900", document_id="doc-900", storage_uri="sha256/ab/cd/deadbeefcafe")
    session.pages[page.id] = page

    ext = Extraction(
        id="ext-900",
        page_id=page.id,
        field_name="owner_name",
        raw_value="Ramesh Kumar",
        canonical_value="Ramesh Kumar",
        bbox="[0,0,10,10]",
        model_version="hwr_v1",
        config_version="cfg_v1",
    )
    session.extractions[ext.id] = ext

    # 1. High edit distance on maker-checker field class lands in PendingCorrection
    res = submit_correction(
        session,
        extraction=ext,
        corrected="Entirely Different Owner",
        actor="officer-01",
        stream="routed",
    )
    assert isinstance(res, PendingCorrection)
    assert res.state == "pending"

    # Invariant: Zero outbox messages emitted while pending
    outbox_msgs = [m for m in session.added if isinstance(m, OutboxMessage)]
    assert len(outbox_msgs) == 0

    # Invariant: First actor cannot confirm own pending correction (FR-REV-12)
    with pytest.raises(SameActorCannotConfirm):
        confirm_pending_correction(session, res.id, actor="officer-01")

    # 2. Distinct second actor confirms
    correction = confirm_pending_correction(session, res.id, actor="officer-02")
    assert isinstance(correction, Correction)
    assert res.state == "confirmed"
    assert res.resulting_correction_id == correction.id
    assert correction.actor == "officer-02"

    # Invariant: OutboxMessage is staged only upon confirmation
    outbox_msgs = [m for m in session.added if isinstance(m, OutboxMessage)]
    assert len(outbox_msgs) == 1
    msg = outbox_msgs[0]
    assert msg.queue == "LEARNING_LOOP_QUEUE"
    assert msg.envelope["producer"] == "review"
    assert msg.envelope["payload"]["id"] == correction.id
    assert msg.envelope["payload"]["source_page_digest"] == "deadbeefcafe"
    assert msg.envelope["payload"]["stream"] == "routed"

    # Invariant: Duplicate confirmation is rejected
    with pytest.raises(PendingCorrectionAlreadyResolved):
        confirm_pending_correction(session, res.id, actor="officer-03")
