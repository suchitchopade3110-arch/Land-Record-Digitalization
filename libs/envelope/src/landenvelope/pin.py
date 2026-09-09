"""Pin-once-at-triage / read-only-thereafter (FR-TRI-09).

`pin()` is called exactly once per page, by `services/backend`'s triage
router, after Tharun's classifiers have produced their outputs for that
page (API-Contracts-and-Interfaces.md §2). Every stage after triage calls
`read()`, never `pin()` again for the same page — a retried message after
a model promotion must reproduce the original result, not a new one.
"""
from __future__ import annotations

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


def pin(
    session: Session,
    *,
    document_id: str,
    page_id: str,
    model_versions: dict[str, str],
    config_version: str,
) -> WorkEnvelope:
    """Write a new, immutable `WorkEnvelope` row. Does not commit — the
    caller (triage_router) commits it in the same transaction as its
    outbox write (ADR-005), so "pinned the envelope" and "queued the next
    hop" succeed or fail together.
    """
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
    session.add(envelope)
    session.flush()  # assigns envelope_id via the column default, without committing
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
