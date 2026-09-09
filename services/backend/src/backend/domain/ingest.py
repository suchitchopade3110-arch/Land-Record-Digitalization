"""FR-ING-01/02/04/05 — batch ingest, custody, re-upload recognition,
mandatory batch metadata. `backend.workers.ingestion_consumer` (FR-ING-03,
page split) and `backend.domain.page_split` pick up from here — this
module's job stops at "hashed it, stored two independent copies, recorded
a `SourceDocument`, told the split worker" and deliberately does not parse
page structure itself.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import IO

from landaudit import append as audit_append
from landoutbox import write as outbox_write
from landstorage import (
    ObjectStorePort,
    assert_distinct_stores,
    get_secondary_store,
    get_store,
)
from observability.envelope import emit
from sqlalchemy.orm import Session

from backend.models.entities import Batch, SourceDocument

# FR-ING-01 — JPEG, PNG, multi-page TIFF, PDF (native and scanned). The
# value is the short format tag `backend.domain.page_split` dispatches on;
# it is not otherwise meaningful outside this module.
SUPPORTED_MIME_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/tiff": "tiff",
    "application/pdf": "pdf",
}


class UnsupportedMediaType(ValueError):
    """FR-ING-01 — "reject unsupported MIME with a machine-readable
    reason code," not just an HTTP 4xx and a sentence. `reason_code` is
    what a caller branches on."""

    def __init__(self, mime: str):
        self.reason_code = "unsupported_mime_type"
        self.mime = mime
        super().__init__(f"unsupported MIME type {mime!r} — supported: {sorted(SUPPORTED_MIME_TYPES)}")


class MissingMandatoryBatchField(ValueError):
    """FR-ING-05 — district is mandatory and rejected at ingest, because
    unit conversion depends on it and discovering the gap at normalize is
    far more expensive than refusing the batch at the door."""

    def __init__(self, field: str):
        self.reason_code = f"missing_mandatory_field:{field}"
        self.field = field
        super().__init__(f"batch metadata missing mandatory field {field!r} (FR-ING-05)")


def validate_mime(mime: str) -> None:
    if mime not in SUPPORTED_MIME_TYPES:
        raise UnsupportedMediaType(mime)


def validate_batch_metadata(*, district: str | None) -> None:
    if not district or not district.strip():
        raise MissingMandatoryBatchField("district")


def get_or_create_batch(
    session: Session,
    *,
    batch_id: str | None = None,
    district: str,
    tehsil: str | None = None,
    village: str | None = None,
    series: str | None = None,
    custodian: str | None = None,
    scanning_date: datetime | None = None,
) -> Batch:
    """FR-ING-05. `district` is validated even when reusing an existing
    `batch_id` — a caller can't sidestep the mandatory-field check by
    passing a stale/forged id for a batch that was never actually
    created."""
    validate_batch_metadata(district=district)
    if batch_id is not None:
        existing = session.get(Batch, batch_id)
        if existing is not None:
            return existing
    batch = Batch(
        district=district, tehsil=tehsil, village=village, series=series,
        custodian=custodian, scanning_date=scanning_date,
    )
    session.add(batch)
    session.flush()
    return batch


@dataclass(frozen=True)
class IngestResult:
    document_id: str
    batch_id: str
    sha256: str
    storage_key: str
    created: bool  # False => FR-ING-04 dedupe — no additional processing work
    queued_for_split: bool  # False whenever created is False (T2.a: zero additional queue messages)


def ingest_document(
    session: Session,
    *,
    batch: Batch,
    stream: IO[bytes],
    mime: str,
    primary_store: ObjectStorePort | None = None,
    secondary_store: ObjectStorePort | None = None,
) -> IngestResult:
    """FR-ING-01/02/04. Streams `stream` through custody — SHA-256 computed
    incrementally, never buffering the whole file (`landstorage.put_stream`
    — a 500-page scanned PDF never sits in memory) — and either:

    - links to the existing `SourceDocument` for this digest and returns
      immediately, enqueueing nothing (FR-ING-04: re-upload recognition,
      the property T2.a asserts as "zero additional queue messages"); or
    - creates a new `SourceDocument` and enqueues exactly one
      `INGESTION_QUEUE` message (via the transactional outbox, ADR-005,
      so "recorded custody" and "told the split worker" commit together)
      for `backend.workers.ingestion_consumer` to split into pages
      (FR-ING-03) — deliberately not done here; custody and page-structure
      parsing are different concerns.

    Also writes the same bytes to a second, independently-credentialed
    store (FR-ING-07's dual-copy requirement) — `assert_distinct_stores()`
    runs first so a deploy that accidentally points both at the same
    location fails loudly instead of silently keeping one copy.
    """
    validate_mime(mime)
    # Only validate distinctness for the *default*, env-driven stores —
    # a caller that injects its own primary_store/secondary_store (tests,
    # or a future caller with its own distinctness guarantee) has already
    # made that call itself; re-validating env vars neither store
    # necessarily came from would just be checking the wrong thing.
    if primary_store is None and secondary_store is None:
        assert_distinct_stores()
    store = primary_store or get_store()
    second_store = secondary_store or get_secondary_store()

    put_result = store.put_stream(stream)
    # Re-read from the primary via its content-addressed key (the caller's
    # `stream` is already exhausted by put_stream above) — this is itself
    # a streaming read (landstorage.open_stream), never a full in-memory
    # copy, so the second write costs no more memory than the first.
    with closing(store.open_stream(put_result.key)) as reread:
        second_store.put_stream(reread)

    existing = session.query(SourceDocument).filter_by(sha256=put_result.digest).first()
    if existing is not None:
        audit_append(
            session, actor="system:ingest", action="ingest.document.deduped",
            subject=existing.id, purpose="FR-ING-04",
        )
        return IngestResult(
            document_id=existing.id, batch_id=existing.batch_id, sha256=put_result.digest,
            storage_key=put_result.key, created=False, queued_for_split=False,
        )

    doc = SourceDocument(
        batch_id=batch.id, sha256=put_result.digest, storage_uri=put_result.key,
        mime=mime, page_count=0,  # filled in by the split worker once it knows the real count
    )
    session.add(doc)
    session.flush()

    # Document-scoped, not page-scoped — there is no Page yet, so the
    # document_id:page_id trace_id format (ADR-002) doesn't apply until
    # after page split; `doc.id` alone is still greppable to this ingest
    # event specifically.
    envelope_msg = emit(
        queue="INGESTION_QUEUE", producer="ingest",
        payload={"document_id": doc.id, "storage_key": put_result.key, "mime": mime},
        work_envelope=None, trace_id=doc.id,
    )
    outbox_write(session, queue="INGESTION_QUEUE", envelope=envelope_msg)
    audit_append(session, actor="system:ingest", action="ingest.document.received", subject=doc.id, purpose="FR-ING-02")

    return IngestResult(
        document_id=doc.id, batch_id=batch.id, sha256=put_result.digest,
        storage_key=put_result.key, created=True, queued_for_split=True,
    )
