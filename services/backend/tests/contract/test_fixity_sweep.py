"""T2.c — Fixity. An object corrupted directly in the store is detected
by the next sweep, not at retrieval. The mismatch raises a dashboard-
visible alert carrying `document_id`.
"""
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.domain.fixity import run_sweep
from backend.models.entities import Batch, FixityCheck, SourceDocument
from landstorage.drivers.local_fs import LocalFsObjectStore

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM work_envelope LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture
def stores():
    with tempfile.TemporaryDirectory() as primary, tempfile.TemporaryDirectory() as secondary:
        yield LocalFsObjectStore(root=primary), LocalFsObjectStore(root=secondary)


def _stored_document(session, primary, secondary, data: bytes) -> SourceDocument:
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    put_result = primary.put(data)
    secondary.put(data)
    doc = SourceDocument(batch_id=batch.id, sha256=put_result.digest, storage_uri=put_result.key, mime="application/pdf", page_count=1)
    session.add(doc)
    session.flush()
    return doc


def test_an_untampered_document_sweeps_clean_on_both_copies(session, stores):
    primary, secondary = stores
    doc = _stored_document(session, primary, secondary, b"pristine bytes")

    alerts = run_sweep(session, document_ids=[doc.id], primary_store=primary, secondary_store=secondary)
    session.flush()

    assert alerts == []
    checks = session.query(FixityCheck).filter_by(document_id=doc.id).all()
    assert {c.store for c in checks} == {"primary", "secondary"}
    assert all(c.outcome == "match" for c in checks)


def test_corruption_written_directly_to_the_store_is_detected_by_the_next_sweep_not_at_retrieval(session, stores):
    """The key property: nothing about *retrieving* the object triggers
    detection — only the sweep does."""
    primary, secondary = stores
    doc = _stored_document(session, primary, secondary, b"the one irreplaceable artifact")

    # Corrupt the primary copy directly on disk, bypassing the store's own
    # API entirely — this is what bit rot or an out-of-band write would
    # look like. No call to primary.get()/verify_fixity() happens here.
    on_disk = Path(primary._path(doc.storage_uri))
    on_disk.write_bytes(b"corrupted!!")

    alerts = run_sweep(session, document_ids=[doc.id], primary_store=primary, secondary_store=secondary)
    session.flush()

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.document_id == doc.id  # dashboard-visible alert carries document_id
    assert alert.store == "primary"

    mismatch = session.query(FixityCheck).filter_by(document_id=doc.id, store="primary").one()
    assert mismatch.outcome == "mismatch"
    assert mismatch.expected_digest == doc.sha256

    # The secondary copy is untouched and still checks out — the whole
    # point of the independent second credential set.
    secondary_check = session.query(FixityCheck).filter_by(document_id=doc.id, store="secondary").one()
    assert secondary_check.outcome == "match"


def test_a_mismatch_writes_an_audit_entry(session, stores):
    from landaudit.chain import shard_for
    from landaudit.models import AuditEntry

    primary, secondary = stores
    doc = _stored_document(session, primary, secondary, b"another artifact")
    Path(primary._path(doc.storage_uri)).write_bytes(b"tampered")

    run_sweep(session, document_ids=[doc.id], primary_store=primary, secondary_store=secondary)
    session.flush()

    entries = session.query(AuditEntry).filter_by(shard_id=shard_for(doc.id), subject=doc.id).all()
    assert any(e.action == "fixity_check.mismatch" for e in entries)
