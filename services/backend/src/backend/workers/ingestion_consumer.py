"""Consumes INGESTION_QUEUE — the backend-internal hop between custody
(`backend.domain.ingest`, which already ran by the time a message reaches
here) and page split (FR-ING-03). Publishes one `TRIAGE_QUEUE` message per
resulting page — `contracts/asyncapi/triage-queue.yaml`'s "Ingest ->
Triage" hop — via the transactional outbox (ADR-005), so "this page's row
exists" and "triage was told about it" commit together.

Idempotent per ADR-005/§04: a redelivered INGESTION_QUEUE message for a
document that already has `Page` rows is a no-op, not a second split —
this is what keeps a re-delivered (not just a re-uploaded) message from
doubling page rows or TRIAGE_QUEUE messages.
"""
from __future__ import annotations

import shutil
import tempfile
from typing import IO

from landoutbox import write as outbox_write
from landstorage import ObjectStorePort, get_store
from observability import traced_consumer
from observability.envelope import emit
from sqlalchemy.orm import Session

from backend.domain.page_split import split_stream
from backend.models.entities import Page, SourceDocument


def _materialize_seekable(store: ObjectStorePort, key: str, *, max_size_in_memory: int = 10 * 1024 * 1024) -> IO[bytes]:
    """Some object-store backends hand back a non-seekable stream (S3's
    StreamingBody); pypdf/Pillow need random access to walk a page tree or
    frame index. Spool to a bounded-in-memory, spill-to-disk tempfile —
    never the process's RAM budget for a large document — positioned at 0
    when this returns. Mirrors `landstorage.hash_stream_to_spooled_tempfile`'s
    discipline without recomputing a digest (already known — this is a
    read of an object already accepted into custody)."""
    # Not a `with` block (ruff SIM115) — returned open; the caller closes
    # it once split_stream() is done reading from it.
    spooled = tempfile.SpooledTemporaryFile(max_size=max_size_in_memory)  # noqa: SIM115
    src = store.open_stream(key)
    try:
        shutil.copyfileobj(src, spooled)
    finally:
        src.close()
    spooled.seek(0)
    return spooled


@traced_consumer
def handle(message: dict, session: Session, *, store: ObjectStorePort | None = None) -> dict:
    payload = message["payload"]
    document_id = payload["document_id"]
    storage_key = payload["storage_key"]
    mime = payload["mime"]

    already_split = session.query(Page).filter(Page.document_id == document_id).count() > 0
    if already_split:
        return {"document_id": document_id, "pages_created": 0, "already_split": True}

    store = store or get_store()
    spooled = _materialize_seekable(store, storage_key)
    try:
        created = 0
        for split_page in split_stream(mime, spooled):
            page_put = store.put(split_page.data)  # one page's bytes — bounded size, bytes-based put() is fine here
            page = Page(document_id=document_id, index=split_page.index, storage_uri=page_put.key)
            session.add(page)
            session.flush()  # assigns page.id

            trace_id = f"{document_id}:{page.id}"
            envelope_msg = emit(
                queue="TRIAGE_QUEUE", producer="ingest",
                payload={
                    "id": page.id, "document_id": document_id, "index": split_page.index,
                    "index_position": None, "quality_score": None, "legibility_band": None,
                    "script": None, "language": None, "doc_type": None, "page_role": None,
                    "writer_cluster_id": None, "novelty_score": None,
                },
                work_envelope=None, trace_id=trace_id,
            )
            outbox_write(session, queue="TRIAGE_QUEUE", envelope=envelope_msg)
            created += 1
    finally:
        spooled.close()

    doc = session.get(SourceDocument, document_id)
    if doc is not None:
        doc.page_count = created

    return {"document_id": document_id, "pages_created": created, "already_split": False}
