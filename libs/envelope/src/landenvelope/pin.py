"""Pin-once-at-triage / read-only-thereafter (FR-TRI-09).

`pin()` is called by `services/backend`'s triage router, after Tharun's
classifiers have produced their outputs for a page
(API-Contracts-and-Interfaces.md §2). Every stage after triage calls
`read()`, never `pin()` again for the same page — a retried message after
a model promotion must reproduce the original result, not a new one.

That replay guarantee is what makes `pin()` itself idempotent per
`page_id` rather than a bare insert: a second `pin()` call for a page that
already has an envelope returns the *existing* row unchanged, ignoring
whatever `model_versions`/`config_version` the caller passed this time —
this is the actual mechanism behind "a retried triage message reproduces
its original result even after the active model version is bumped"
(T2.d). `infra/migrations` backs this with a real unique constraint on
`work_envelope.page_id` (see `0003_ingest_and_replay_hardening.py`) so the
guarantee holds even against a caller that bypasses this function and
inserts via raw SQL — belt and suspenders, matching this repo's other
invariants (CLAUDE.md).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from landenvelope.models import WorkEnvelope

REQUIRED_MODEL_KEYS = {
    "triage_classifier",
    "printed_ocr",
    "hwr",
    "confidence_calibrator",
    "novelty_detector",
}


class IncompleteModelVersions(ValueError):
    """Raised if `pin()` is called without all five required model
    versions — matches `work_envelope.schema.json`'s `required` list
    exactly, so a caller can't accidentally pin a partial envelope that
    would fail contract validation downstream."""


def find_by_page(session: Session, page_id: str) -> WorkEnvelope | None:
    """The envelope already pinned for `page_id`, if any — the lookup that
    makes `pin()` (and, more importantly, callers that want to skip
    re-resolving model versions entirely on a replay, per API-Contracts §7
    rule 1) idempotent."""
    return session.execute(select(WorkEnvelope).where(WorkEnvelope.page_id == page_id)).scalar_one_or_none()


def pin(
    session: Session,
    *,
    document_id: str,
    page_id: str,
    model_versions: dict[str, str],
    config_version: str,
) -> WorkEnvelope:
    """Write the immutable `WorkEnvelope` row for `page_id` — or, if one
    already exists (a replayed/redelivered triage message), return that
    existing row unchanged rather than pinning a second one. Does not
    commit — the caller (triage_router) commits it in the same transaction
    as its outbox write (ADR-005), so "pinned the envelope" and "queued the
    next hop" succeed or fail together.
    """
    existing = find_by_page(session, page_id)
    if existing is not None:
        return existing

    missing = REQUIRED_MODEL_KEYS - model_versions.keys()
    if missing:
        raise IncompleteModelVersions(f"missing model_versions keys: {sorted(missing)}")
    extra = model_versions.keys() - REQUIRED_MODEL_KEYS
    if extra:
        raise IncompleteModelVersions(f"unexpected model_versions keys: {sorted(extra)}")

    envelope = WorkEnvelope(
        document_id=document_id,
        page_id=page_id,
        model_versions=dict(model_versions),
        config_version=config_version,
    )
    try:
        # A SAVEPOINT (not session.rollback()) so a lost race only undoes
        # this insert, not whatever else the caller's own transaction has
        # already staged (e.g. route_page's outbox write, ADR-005) —
        # `infra/migrations`' unique constraint on work_envelope.page_id is
        # what actually catches the race; this is just how we recover from
        # it without collateral damage to the rest of the transaction.
        with session.begin_nested():
            session.add(envelope)
            session.flush()  # assigns envelope_id via the column default, without committing
    except IntegrityError:
        winner = find_by_page(session, page_id)
        if winner is None:  # pragma: no cover — should be unreachable
            raise
        return winner
    return envelope


def read(session: Session, envelope_id: str) -> WorkEnvelope:
    """The only other operation this module exposes. There is
    deliberately no `update()` — the DB trigger installed by
    `infra/migrations/versions/0002_core_schema.py` rejects an UPDATE at
    the storage layer regardless, but the absence of the method here means
    nobody can even try from Python."""
    envelope = session.get(WorkEnvelope, envelope_id)
    if envelope is None:
        raise KeyError(f"no WorkEnvelope with envelope_id={envelope_id!r}")
    return envelope
