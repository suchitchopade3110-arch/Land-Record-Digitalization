"""Targets its own scratch database (`LIB_TEST_DATABASE_URL`, default
`landrecords_libtest`), not `landrecords_test` — this fixture's
create_all/drop_all must never touch the Alembic-migrated database that
tests/invariant/ and services/backend/tests/contract/ depend on staying
intact across a `make verify` run. See libs/audit/tests/test_chain.py's
module docstring for the full reasoning; the same hazard applies here."""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from landenvelope.models import EnvelopeBase
from landenvelope.pin import IncompleteModelVersions, pin, read

TEST_DB_URL = os.environ.get(
    "LIB_TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_libtest"
)

VALID_MODEL_VERSIONS = {
    "triage_classifier": "v1",
    "printed_ocr": "v3",
    "hwr": "v2",
    "confidence_calibrator": "v1",
    "novelty_detector": "v1",
}


@pytest.fixture
def session():
    try:
        engine = create_engine(TEST_DB_URL, future=True)
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip(f"no local Postgres at {TEST_DB_URL}")
    EnvelopeBase.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    EnvelopeBase.metadata.drop_all(engine)


def test_pin_then_read_roundtrips_the_contract_shape(session):
    envelope = pin(
        session,
        document_id="doc-1",
        page_id="page-1",
        model_versions=VALID_MODEL_VERSIONS,
        config_version="cfg-v7",
    )
    session.commit()

    fetched = read(session, envelope.envelope_id)
    contract_dict = fetched.to_contract_dict()

    assert set(contract_dict) == {
        "envelope_id", "document_id", "page_id", "pinned_at", "model_versions", "config_version",
    }
    assert contract_dict["model_versions"] == VALID_MODEL_VERSIONS


def test_pin_rejects_missing_model_version_keys(session):
    incomplete = dict(VALID_MODEL_VERSIONS)
    del incomplete["hwr"]

    with pytest.raises(IncompleteModelVersions):
        pin(session, document_id="d", page_id="p", model_versions=incomplete, config_version="v1")


def test_pin_rejects_unexpected_model_version_keys(session):
    extra = dict(VALID_MODEL_VERSIONS, some_future_model="v1")

    with pytest.raises(IncompleteModelVersions):
        pin(session, document_id="d", page_id="p", model_versions=extra, config_version="v1")


def test_read_of_unknown_envelope_raises_keyerror(session):
    with pytest.raises(KeyError):
        read(session, "00000000-0000-0000-0000-000000000000")
