"""Invariant 3 (CLAUDE.md) / FR-TRI-09 — WorkEnvelope is immutable after
write. A DB trigger (`work_envelope_reject_update`, installed by
`infra/migrations/versions/0002_core_schema.py`) rejects any UPDATE,
independent of whether the caller went through `landenvelope.pin`'s
Python API (which exposes no update method at all) or issued raw SQL."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from landenvelope import pin

VALID_MODEL_VERSIONS = {
    "triage_classifier": "v1",
    "printed_ocr": "v3",
    "hwr": "v2",
    "confidence_calibrator": "v1",
    "novelty_detector": "v1",
}


def test_a_freshly_pinned_envelope_can_be_read_back_unchanged(session):
    envelope = pin(
        session, document_id="doc-1", page_id="page-1",
        model_versions=VALID_MODEL_VERSIONS, config_version="cfg-v1",
    )
    session.commit()

    assert envelope.config_version == "cfg-v1"


def test_updating_a_pinned_envelope_via_raw_sql_is_REJECTED_by_the_database(session):
    """The property that matters: even a caller that bypasses
    `landenvelope`'s Python API entirely and updates the table directly
    cannot mutate a pinned envelope — this is a storage-layer guarantee,
    not an application-level convention."""
    envelope = pin(
        session, document_id="doc-2", page_id="page-2",
        model_versions=VALID_MODEL_VERSIONS, config_version="cfg-v1",
    )
    session.commit()

    with pytest.raises(ProgrammingError, match="work_envelope is immutable after write"):
        session.execute(
            text("UPDATE work_envelope SET config_version = :new_cfg WHERE envelope_id = :id"),
            {"new_cfg": "cfg-v2-should-never-land", "id": envelope.envelope_id},
        )
        session.commit()
    session.rollback()

    # And the value on disk is provably unchanged after the rejected attempt.
    reread = session.execute(
        text("SELECT config_version FROM work_envelope WHERE envelope_id = :id"), {"id": envelope.envelope_id}
    ).scalar_one()
    assert reread == "cfg-v1"


def test_updating_via_the_orm_is_also_rejected(session):
    """Same guarantee reached through SQLAlchemy's ORM update path, not
    just raw SQL — belt and suspenders against a future caller that adds
    an `update()` helper to `landenvelope` without reading this test."""
    from landenvelope.models import WorkEnvelope

    envelope = pin(
        session, document_id="doc-3", page_id="page-3",
        model_versions=VALID_MODEL_VERSIONS, config_version="cfg-v1",
    )
    session.commit()

    row = session.get(WorkEnvelope, envelope.envelope_id)
    row.config_version = "attempted-mutation"
    with pytest.raises(ProgrammingError, match="work_envelope is immutable after write"):
        session.commit()
    session.rollback()
