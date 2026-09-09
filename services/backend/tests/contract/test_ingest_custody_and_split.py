"""T2.a — Acceptance (PRD M1). A 500-page scanned PDF ingested in one
operation yields 500 addressable pages. Stored digest matches the upload.
Re-uploading the identical file creates zero additional processing work.

Real Postgres (`TEST_DATABASE_URL`) + a real `local_fs` object store
(a temp dir) — the whole custody -> outbox -> split path, not a mock of it.

Uses `session.flush()`, not `.commit()` — every assertion below reads
through the same open session/transaction (flush already makes writes
visible to it), and the fixture's teardown `rollback()` then leaves zero
permanent footprint in the shared test database. This matters
concretely here: the 500-page test would otherwise leave 500 real,
permanently-undispatched `TRIAGE_QUEUE` outbox rows behind — enough on
its own to push `tests/e2e`'s `Relay.drain_once(batch_size=100)` (which
drains the globally oldest undispatched rows, not filtered by queue) past
its cap in the same `make verify` run, making an unrelated, pre-existing
test fail for a reason that has nothing to do with it.
"""
import io
import os
import tempfile
import uuid

import pytest
from pypdf import PdfWriter
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.domain.ingest import (
    MissingMandatoryBatchField,
    UnsupportedMediaType,
    get_or_create_batch,
    ingest_document,
)
from backend.models.entities import Page, SourceDocument
from backend.workers.ingestion_consumer import handle as ingestion_consumer_handle
from landoutbox.models import OutboxMessage
from landstorage.drivers.local_fs import LocalFsObjectStore
from landstorage.port import digest_of

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


def _make_pdf_bytes(num_pages: int) -> bytes:
    """A random nonce in the metadata keeps every call's bytes (and so
    its digest) unique — pypdf's blank-page output is otherwise fully
    deterministic, which would make two separate test runs against the
    same shared Postgres collide on FR-ING-04 dedupe by accident (this
    test module deliberately controls when dedupe fires, in
    `test_reuploading_the_identical_file_creates_zero_additional_queue_messages`,
    by reusing the exact same bytes on purpose)."""
    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/Nonce": uuid.uuid4().hex})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_a_500_page_scanned_pdf_yields_500_addressable_pages_with_a_matching_digest(session, stores):
    primary, secondary = stores
    data = _make_pdf_bytes(500)

    batch = get_or_create_batch(session, district="sitapur")
    result = ingest_document(
        session, batch=batch, stream=io.BytesIO(data), mime="application/pdf",
        primary_store=primary, secondary_store=secondary,
    )
    session.flush()

    assert result.created is True
    assert result.sha256 == digest_of(data)

    doc = session.get(SourceDocument, result.document_id)
    assert doc.sha256 == digest_of(data)
    assert primary.get(doc.storage_uri) == data
    assert secondary.get(doc.storage_uri) == data  # FR-ING-07 — independent second copy

    outbox_row = (
        session.query(OutboxMessage)
        .filter_by(queue="INGESTION_QUEUE")
        .order_by(OutboxMessage.created_at.desc())
        .first()
    )
    assert outbox_row is not None

    split_result = ingestion_consumer_handle(outbox_row.envelope, session, store=primary)
    session.flush()

    assert split_result["pages_created"] == 500
    pages = session.query(Page).filter_by(document_id=doc.id).order_by(Page.index.asc()).all()
    assert len(pages) == 500
    assert [p.index for p in pages] == list(range(500))
    assert all(p.storage_uri is not None for p in pages)
    assert all(p.doc_type is None and p.page_role is None for p in pages)  # pre-classification

    refreshed_doc = session.get(SourceDocument, doc.id)
    assert refreshed_doc.page_count == 500


def test_reuploading_the_identical_file_creates_zero_additional_queue_messages(session, stores):
    """FR-ING-04. Asserts the *count* of INGESTION_QUEUE outbox rows does
    not increase on the second ingest of byte-identical content — not just
    that the document id is reused."""
    primary, secondary = stores
    data = b"the exact same bytes, twice"
    batch = get_or_create_batch(session, district="sitapur")

    first = ingest_document(
        session, batch=batch, stream=io.BytesIO(data), mime="application/pdf",
        primary_store=primary, secondary_store=secondary,
    )
    session.flush()

    before = session.query(OutboxMessage).filter_by(queue="INGESTION_QUEUE").count()

    second = ingest_document(
        session, batch=batch, stream=io.BytesIO(data), mime="application/pdf",
        primary_store=primary, secondary_store=secondary,
    )
    session.flush()

    after = session.query(OutboxMessage).filter_by(queue="INGESTION_QUEUE").count()

    assert second.document_id == first.document_id
    assert second.created is False
    assert second.queued_for_split is False
    assert after == before  # zero additional queue messages


def test_ingesting_a_redelivered_ingestion_queue_message_does_not_double_the_pages(session, stores):
    """ADR-005: every consumer must be idempotent against redelivery, not
    just against a re-upload at the HTTP layer."""
    primary, secondary = stores
    data = _make_pdf_bytes(5)
    batch = get_or_create_batch(session, district="sitapur")
    result = ingest_document(
        session, batch=batch, stream=io.BytesIO(data), mime="application/pdf",
        primary_store=primary, secondary_store=secondary,
    )
    session.flush()

    outbox_row = session.query(OutboxMessage).filter_by(queue="INGESTION_QUEUE").order_by(
        OutboxMessage.created_at.desc()
    ).first()

    first_run = ingestion_consumer_handle(outbox_row.envelope, session, store=primary)
    session.flush()
    second_run = ingestion_consumer_handle(outbox_row.envelope, session, store=primary)
    session.flush()

    assert first_run["pages_created"] == 5
    assert second_run["already_split"] is True
    assert second_run["pages_created"] == 0
    assert session.query(Page).filter_by(document_id=result.document_id).count() == 5


def test_unsupported_mime_is_rejected_with_a_machine_readable_reason_code(session, stores):
    primary, secondary = stores
    batch = get_or_create_batch(session, district="sitapur")
    with pytest.raises(UnsupportedMediaType) as exc_info:
        ingest_document(
            session, batch=batch, stream=io.BytesIO(b"whatever"), mime="application/x-msdownload",
            primary_store=primary, secondary_store=secondary,
        )
    assert exc_info.value.reason_code == "unsupported_mime_type"


def test_batch_without_district_is_rejected_at_ingest_FR_ING_05(session):
    with pytest.raises(MissingMandatoryBatchField) as exc_info:
        get_or_create_batch(session, district="")
    assert exc_info.value.field == "district"
