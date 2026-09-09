"""FR-ING-07 — scheduled fixity sweep. Re-hashes every stored original
(both the primary and secondary copies — P2-06's dual-copy requirement,
`assert_distinct_stores` having already confirmed at startup that the two
are genuinely independent) against its recorded digest, streaming (never
loading a whole object into memory just to check it hasn't changed — the
same discipline custody itself uses, `hash_stream_to_spooled_tempfile`).
Writes one `FixityCheck` row per object per store, and raises a
dashboard-visible alert on mismatch naming `document_id` — detected on the
next sweep, not at retrieval (T2.c): nothing here depends on anyone having
read the object recently.

The dashboard itself (M14) is Phase 5 scope and not yet built — `FixityAlert`
is the domain event that dashboard aggregation will read once it exists;
until then, the audit-log entry this sweep also writes is the durable,
queryable record of every mismatch.
"""
from __future__ import annotations

from dataclasses import dataclass

from landaudit import append as audit_append
from landstorage import (
    ObjectStorePort,
    assert_distinct_stores,
    get_secondary_store,
    get_store,
)
from landstorage.port import hash_stream_to_spooled_tempfile
from sqlalchemy.orm import Session

from backend.models.entities import FixityCheck, SourceDocument


@dataclass(frozen=True)
class FixityAlert:
    document_id: str
    store: str  # "primary" | "secondary"
    expected_digest: str
    observed_digest: str


def _rehash(backing: ObjectStorePort, key: str) -> str:
    stream = backing.open_stream(key)
    try:
        digest, spooled = hash_stream_to_spooled_tempfile(stream)
        spooled.close()
        return digest
    finally:
        stream.close()


def run_sweep(
    session: Session,
    *,
    document_ids: list[str] | None = None,
    primary_store: ObjectStorePort | None = None,
    secondary_store: ObjectStorePort | None = None,
) -> list[FixityAlert]:
    """Sweep every `SourceDocument`, or only `document_ids` if given (a
    scheduled production run sweeps everything; a targeted re-check of one
    batch's documents — or a test using a scratch object store — passes
    the filter instead)."""
    # Same reasoning as backend.domain.ingest.ingest_document: only
    # validate the default, env-driven stores — a caller injecting its
    # own primary/secondary store has already made that call itself.
    if primary_store is None and secondary_store is None:
        assert_distinct_stores()
    store = primary_store or get_store()
    second_store = secondary_store or get_secondary_store()

    query = session.query(SourceDocument)
    if document_ids is not None:
        query = query.filter(SourceDocument.id.in_(document_ids))

    alerts: list[FixityAlert] = []
    for doc in query.all():
        for store_label, backing in (("primary", store), ("secondary", second_store)):
            observed_digest = _rehash(backing, doc.storage_uri)
            ok = observed_digest == doc.sha256
            session.add(
                FixityCheck(
                    document_id=doc.id, expected_digest=doc.sha256, observed_digest=observed_digest,
                    outcome="match" if ok else "mismatch", store=store_label,
                )
            )
            if not ok:
                audit_append(
                    session, actor="system:fixity-sweep", action="fixity_check.mismatch",
                    subject=doc.id, purpose=f"FR-ING-07:{store_label}",
                )
                alerts.append(
                    FixityAlert(document_id=doc.id, store=store_label, expected_digest=doc.sha256, observed_digest=observed_digest)
                )
    session.flush()
    return alerts
