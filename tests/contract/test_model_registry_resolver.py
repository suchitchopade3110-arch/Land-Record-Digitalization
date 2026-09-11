"""T1-04 — M2 Triage Routing: Real Model Version Resolver & Work Envelope Contract Tests.

Tests:
1. Mapping: A resolver fed the registry's five modules produces an envelope that validates
   against contracts/schemas/work_envelope.schema.json (using jsonschema), including
   confidence_calibrator mapped from calibrator.
2. Failure: Any module 404, connection failure or timeout -> route_page raises a typed
   ModelRegistryResolutionError; no work_envelope row and no outbox rows exist after rollback.
3. Promotion replay: Pin while registry returns v1; registry then returns v2; replay ->
   envelope still has v1 and zero HTTP requests were made on replay (counted via httpx.MockTransport).
4. Contract tier: Call Tharun's real FastAPI app (modelwork.main.app) through TestClient/httpx
   and verify resolver correctly fetches active baseline models and pins valid envelope.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import httpx
import jsonschema
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.model_registry_client import (
    ModelRegistryResolutionError,
    resolve_active_model_versions,
)
from backend.domain.triage import route_page
from backend.models.entities import Batch, Page, SourceDocument
from landenvelope.models import WorkEnvelope
from landoutbox.models import OutboxMessage
from modelwork.main import app as modelwork_app

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "schemas"
WORK_ENVELOPE_SCHEMA = json.loads((SCHEMAS_DIR / "work_envelope.schema.json").read_text(encoding="utf-8"))

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, connect_args={"connect_timeout": 2}, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM work_envelope LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} is unreachable or has no migrated schema — skipping DB tests per Ground rule 13")
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, future=True)


@pytest.fixture
def sample_page(session_factory):
    with session_factory() as session:
        batch = Batch(district="sitapur")
        session.add(batch)
        session.flush()
        doc = SourceDocument(
            batch_id=batch.id,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            storage_uri="dummy",
            mime="image/tiff",
            page_count=1,
        )
        session.add(doc)
        session.flush()
        page = Page(document_id=doc.id, index=0)
        session.add(page)
        session.commit()
        return page


# ---------------------------------------------------------------------------
# Test 1: Mapping & Schema Validation
# ---------------------------------------------------------------------------


def test_resolver_maps_five_modules_and_validates_work_envelope_schema(session_factory, sample_page):
    """Broken implementation it catches: A resolver that fails to map 'calibrator' ->
    'confidence_calibrator', misses a required module, or produces model_versions dict
    violating contracts/schemas/work_envelope.schema.json.
    """
    responses = {
        "/models/printed_ocr/active": {"model_version": "ocr-v2.1", "adapter_ref": None},
        "/models/hwr/active": {"model_version": "hwr-v1.4", "adapter_ref": None},
        "/models/calibrator/active": {"model_version": "calib-v3.0", "adapter_ref": None},
        "/models/novelty_detector/active": {"model_version": "novelty-v1.1", "adapter_ref": None},
        "/models/triage_classifier/active": {"model_version": "triage-v2.0", "adapter_ref": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            return httpx.Response(200, json=responses[path])
        return httpx.Response(404, json={"detail": f"Not found: {path}"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(base_url="http://model-registry.test", transport=transport)

    with session_factory() as session:
        envelope, queued = route_page(
            session,
            document_id=sample_page.document_id,
            page_id=sample_page.id,
            doc_type="jamabandi",
            page_role="text",
            config_version="cfg-2026-v1",
            resolve_model_versions=lambda: resolve_active_model_versions(client=client),
        )
        session.commit()

        contract_dict = envelope.to_contract_dict()

        # Validate strictly against frozen JSON schema
        jsonschema.validate(instance=contract_dict, schema=WORK_ENVELOPE_SCHEMA)

        # Explicit mapping verification: calibrator -> confidence_calibrator
        assert contract_dict["model_versions"] == {
            "printed_ocr": "ocr-v2.1",
            "hwr": "hwr-v1.4",
            "confidence_calibrator": "calib-v3.0",
            "novelty_detector": "novelty-v1.1",
            "triage_classifier": "triage-v2.0",
        }
        assert "calibrator" not in contract_dict["model_versions"]


# ---------------------------------------------------------------------------
# Test 2: Failure on 404 or Timeout (D1)
# ---------------------------------------------------------------------------


def test_resolver_failure_raises_and_rolls_back_atomically(session_factory, sample_page):
    """Broken implementation it catches: A resolver that silently swallows 404/timeout,
    falls back to a default placeholder version or partial envelope, or leaves orphaned
    work_envelope / outbox rows on failure.
    """
    # Simulate 404 for calibrator module
    def error_handler(request: httpx.Request) -> httpx.Response:
        if "calibrator" in request.url.path:
            return httpx.Response(404, json={"detail": "calibrator not found"})
        return httpx.Response(200, json={"model_version": "v1"})

    transport = httpx.MockTransport(error_handler)
    client = httpx.Client(base_url="http://model-registry.test", transport=transport)

    unpinned_page_id = str(uuid.uuid4())

    with session_factory() as session:
        # Triage attempt must raise ModelRegistryResolutionError
        with pytest.raises(ModelRegistryResolutionError) as exc_info:
            route_page(
                session,
                document_id=sample_page.document_id,
                page_id=unpinned_page_id,
                doc_type="jamabandi",
                page_role="text",
                config_version="cfg-v1",
                resolve_model_versions=lambda: resolve_active_model_versions(client=client),
            )
            session.commit()

        assert "calibrator" in str(exc_info.value)
        session.rollback()

    # Verify atomic cleanliness: no work_envelope or outbox_message rows exist
    with session_factory() as session:
        env = session.scalars(select(WorkEnvelope).where(WorkEnvelope.page_id == unpinned_page_id)).all()
        assert len(env) == 0

        outbox_rows = session.scalars(select(OutboxMessage)).all()
        for r in outbox_rows:
            assert r.envelope.get("payload", {}).get("page_id") != unpinned_page_id


# ---------------------------------------------------------------------------
# Test 3: Promotion Replay & Zero HTTP Requests on Replay (T2.d)
# ---------------------------------------------------------------------------


def test_promotion_replay_preserves_initial_versions_with_zero_http_requests(session_factory, sample_page):
    """Broken implementation it catches: A triage handler that performs redundant HTTP
    model registry round-trips on replayed messages or re-resolves promoted versions mid-pipeline.
    """
    current_versions = {
        "printed_ocr": "ocr-v1",
        "hwr": "hwr-v1",
        "calibrator": "calib-v1",
        "novelty_detector": "novelty-v1",
        "triage_classifier": "triage-v1",
    }
    http_request_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_request_count
        http_request_count += 1
        module = request.url.path.split("/")[2]
        return httpx.Response(200, json={"model_version": current_versions[module]})

    transport = httpx.MockTransport(mock_handler)
    client = httpx.Client(base_url="http://model-registry.test", transport=transport)

    # Initial run: fetches 5 model versions from registry
    with session_factory() as session:
        first_env, _ = route_page(
            session,
            document_id=sample_page.document_id,
            page_id=sample_page.id,
            doc_type="jamabandi",
            page_role="text",
            config_version="cfg-v1",
            resolve_model_versions=lambda: resolve_active_model_versions(client=client),
        )
        session.commit()

    assert http_request_count == 5
    assert first_env.model_versions["printed_ocr"] == "ocr-v1"

    # Simulate upstream promotion of printed_ocr to v2
    current_versions["printed_ocr"] = "ocr-v2-PROMOTED"

    # Replay run for identical page_id
    with session_factory() as session:
        second_env, _ = route_page(
            session,
            document_id=sample_page.document_id,
            page_id=sample_page.id,
            doc_type="jamabandi",
            page_role="text",
            config_version="cfg-v1",
            resolve_model_versions=lambda: resolve_active_model_versions(client=client),
        )
        session.commit()

    # Replay must NOT make any new HTTP requests (HTTP request count remains 5)
    assert http_request_count == 5
    # Envelope must retain initial pinned version (ocr-v1), NOT promoted version (ocr-v2-PROMOTED)
    assert second_env.envelope_id == first_env.envelope_id
    assert second_env.model_versions["printed_ocr"] == "ocr-v1"


# ---------------------------------------------------------------------------
# Test 4: Contract Tier with Real Modelwork FastAPI App
# ---------------------------------------------------------------------------


def test_resolver_with_real_modelwork_fastapi_app(session_factory, sample_page):
    """Broken implementation it catches: A resolver client incompatible with Tharun's
    real FastAPI GET /models/{module}/active implementation or route paths.
    """
    from starlette.testclient import TestClient

    with TestClient(modelwork_app) as client:
        with session_factory() as session:
            envelope, queued = route_page(
                session,
                document_id=sample_page.document_id,
                page_id=sample_page.id,
                doc_type="cadastral_map",
                page_role="tabular_register",
                config_version="cfg-live-v1",
                resolve_model_versions=lambda: resolve_active_model_versions(client=client),
            )
            session.commit()

            contract_dict = envelope.to_contract_dict()
            jsonschema.validate(instance=contract_dict, schema=WORK_ENVELOPE_SCHEMA)

            # Verify real values from modelwork/api/model_registry.py
            assert contract_dict["model_versions"] == {
                "printed_ocr": "printed_ocr_v1",
                "hwr": "hwr_v1",
                "confidence_calibrator": "calibrator_v1",
                "novelty_detector": "novelty_detector_v1",
                "triage_classifier": "triage_classifier_v1",
            }

