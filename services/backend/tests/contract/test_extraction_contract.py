"""Validates `Extraction.to_contract_dict()` output against
contracts/schemas/extraction.schema.json — the schema-conformance half of
the Phase 1 gate (`make verify`'s `test-contract` target), now that
backend's DB layer exists (this was a TODO left by the prior scaffolding
pass, deferred until then)."""
import json
import os
from pathlib import Path

import jsonschema
import pytest
from jsonschema import validate
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.models.entities import Batch, Extraction, Page, SourceDocument

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contracts" / "schemas" / "extraction.schema.json"

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), "contracts/schemas/extraction.schema.json must exist"


def test_schema_is_valid_json():
    json.loads(SCHEMA_PATH.read_text())


@pytest.fixture
def session():
    try:
        engine = create_engine(TEST_DB_URL, future=True)
        with engine.connect() as c:
            c.execute(text("SELECT 1 FROM extraction LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture
def a_page(session):
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256="e" * 64, storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0)
    session.add(page)
    session.flush()
    return page


def test_a_fully_populated_extraction_row_conforms_to_the_schema(session, a_page):
    """A row shaped like what Shree's OCR + Tharun's calibrator would
    eventually produce — every schema-required field populated."""
    extraction = Extraction(
        page_id=a_page.id,
        field_name="owner_name",
        raw_value="राम प्रसाद",
        canonical_value="Ram Prasad",
        bbox={"x": 10.0, "y": 20.0, "w": 100.0, "h": 30.0},
        engine="printed_ocr",
        model_version="printed-ocr-v3",
        config_version="cfg-v1",
        token_confidence=0.94,
        calibrated_confidence=0.91,
        novelty_score=0.02,
        routing_outcome="auto_accept",
        attestation_refs=[],
        top_k=[{"value": "Ram Prasad", "confidence": 0.94}],
    )
    session.add(extraction)
    session.flush()

    schema = json.loads(SCHEMA_PATH.read_text())
    validate(instance=extraction.to_contract_dict(), schema=schema)  # raises on nonconformance


def test_an_extraction_row_missing_a_schema_required_field_fails_conformance(session, a_page):
    """The negative case: a row that hasn't finished OCR yet (no `engine`
    set) is a legitimate intermediate DB state (the column is nullable —
    assembly can reference a row before extraction completes) but is NOT
    yet contract-conformant, and this test proves the validator actually
    catches that rather than silently accepting anything."""
    extraction = Extraction(page_id=a_page.id, field_name="owner_name", raw_value="x")  # engine, model_version, etc. all null
    session.add(extraction)
    session.flush()

    schema = json.loads(SCHEMA_PATH.read_text())
    with pytest.raises(jsonschema.exceptions.ValidationError):
        validate(instance=extraction.to_contract_dict(), schema=schema)


def test_extraction_rows_conform(session, a_page):
    """Historical test name, now implemented (was `@pytest.mark.skip`,
    'TODO: validate real Extraction rows once backend's DB layer
    exists') — kept as an alias so anything that referenced this test id
    by name still finds it."""
    test_a_fully_populated_extraction_row_conforms_to_the_schema(session, a_page)
