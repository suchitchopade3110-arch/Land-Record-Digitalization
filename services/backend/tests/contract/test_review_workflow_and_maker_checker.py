"""T3.a (indistinguishability), T3.c (maker-checker), T3.d (correction
fidelity) — plus P3-03/04/05/09's ordinary read/write paths. Same
real-Postgres integration style as
`test_triage_and_decision_routing.py`.
"""
import os

import pytest
from backend.api.serializers import strip_review_task_internals
from backend.domain.correction import (
    SameActorCannotConfirm,
    confirm_pending_correction,
    edit_distance,
    submit_correction,
)
from backend.domain.review_workflow import NotTheClaimant, claim_next, submit
from backend.models.entities import (
    AuditSample,
    Batch,
    Correction,
    Extraction,
    Page,
    PendingCorrection,
    ReviewTask,
    SourceDocument,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM pending_correction LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema (run migrations, including 0004) — skipping")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture
def a_page(session):
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256="c" * 64, storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0, storage_uri="sha256/ab/cd/" + "d" * 64)
    session.add(page)
    session.flush()
    return page


def _extraction(session, page, *, field_name="owner_name", raw_value="Ramesh Kumar"):
    e = Extraction(page_id=page.id, field_name=field_name, raw_value=raw_value, canonical_value=raw_value,
                    model_version="hwr-v1", config_version="cfg-v1")
    session.add(e)
    session.flush()
    return e


# ---- edit_distance (pure function) ----

@pytest.mark.parametrize(
    "a,b,expected",
    [("", "", 0), ("Ramesh", "Ramesh", 0), ("Ramesh", "Rajesh", 1), ("", "abc", 3), ("abc", "", 3)],
)
def test_edit_distance(a, b, expected):
    assert edit_distance(a, b) == expected


# ---- T3.d correction fidelity (P3-08) ----

def test_source_page_digest_is_copied_from_the_page_record_not_recomputed(session, a_page):
    extraction = _extraction(session, a_page)
    correction = submit_correction(
        session, extraction=extraction, corrected="Rajesh Kumar", actor="officer-1", stream="routed",
    )
    session.commit()

    assert isinstance(correction, Correction)
    expected_digest = a_page.storage_uri.rsplit("/", 1)[-1]
    assert correction.source_page_digest == expected_digest


# ---- T3.c maker-checker (P3-07) ----

def test_high_edit_distance_owner_name_correction_lands_in_pending_not_correction(session, a_page):
    extraction = _extraction(session, a_page, field_name="owner_name", raw_value="Ramesh Kumar")
    result = submit_correction(
        session, extraction=extraction, corrected="Someone Entirely Different", actor="officer-1", stream="routed",
    )
    session.commit()

    assert isinstance(result, PendingCorrection)
    assert result.state == "pending"
    assert session.query(Correction).filter_by(extraction_id=extraction.id).count() == 0


def test_low_edit_distance_correction_on_a_maker_checker_field_still_writes_directly(session, a_page):
    extraction = _extraction(session, a_page, field_name="owner_name", raw_value="Ramesh Kumar")
    result = submit_correction(
        session, extraction=extraction, corrected="Ramesh Kumarr", actor="officer-1", stream="routed",
    )
    session.commit()

    assert isinstance(result, Correction)


def test_same_actor_cannot_confirm_their_own_pending_correction(session, a_page):
    extraction = _extraction(session, a_page, field_name="owner_name", raw_value="Ramesh Kumar")
    pending = submit_correction(
        session, extraction=extraction, corrected="Totally Different Name", actor="officer-1", stream="routed",
    )
    session.flush()

    with pytest.raises(SameActorCannotConfirm):
        confirm_pending_correction(session, pending.id, actor="officer-1")


def test_a_distinct_second_actor_confirms_and_only_then_a_correction_row_exists(session, a_page):
    extraction = _extraction(session, a_page, field_name="owner_name", raw_value="Ramesh Kumar")
    pending = submit_correction(
        session, extraction=extraction, corrected="Totally Different Name", actor="officer-1", stream="routed",
    )
    session.flush()
    assert session.query(Correction).filter_by(extraction_id=extraction.id).count() == 0

    correction = confirm_pending_correction(session, pending.id, actor="officer-2")
    session.commit()

    assert correction.actor == "officer-2"
    assert correction.corrected == "Totally Different Name"
    refreshed = session.get(PendingCorrection, pending.id)
    assert refreshed.state == "confirmed"
    assert refreshed.resulting_correction_id == correction.id


# ---- P3-03 claim / P3-05/09 submit ----

def _drain_pool(session):
    """Other test modules in this suite `session.commit()` `ReviewTask`
    rows of their own (e.g. `test_triage_and_decision_routing.py`'s
    decision-engine tests), which — being real commits, not rollbacks —
    persist in the shared test database across files. A claim test that
    asserts "the pool is empty" or "I get *my* task next" has to close
    that pre-existing backlog first, the same "must not depend on running
    in isolation" discipline `test_route_page_and_outbox_write_commit_
    atomically` already documents for this suite."""
    while True:
        t = claim_next(session, actor="test-drain")
        if t is None:
            break
        from datetime import datetime, timezone

        t.closed_at = datetime.now(timezone.utc)
        session.flush()
    session.commit()


def test_claim_next_assigns_and_returns_none_once_the_pool_is_empty(session, a_page):
    _drain_pool(session)
    extraction = _extraction(session, a_page)
    task = ReviewTask(extraction_id=extraction.id, reason="rule failed: name mismatch", source_stream="routed")
    session.add(task)
    session.flush()

    claimed = claim_next(session, actor="officer-1")
    session.commit()

    assert claimed.id == task.id
    assert claimed.assignee == "officer-1"
    assert claimed.hour_into_session == 0
    assert claim_next(session, actor="officer-2") is None


def test_submit_closes_the_task_and_records_an_audit_sample_verdict(session, a_page):
    extraction = _extraction(session, a_page, field_name="survey_number", raw_value="123/A")
    task = ReviewTask(extraction_id=extraction.id, reason=None, source_stream="audit")
    session.add(task)
    session.flush()
    sample = AuditSample(extraction_id=extraction.id, review_task_id=task.id, model_confidence=0.91)
    session.add(sample)
    session.flush()
    # Assign directly rather than through claim_next — this test is about
    # submit()'s write path, not the pool-ordering claim_next already has
    # its own dedicated test for.
    task.assignee = "officer-1"
    session.flush()

    result = submit(session, task_id=task.id, actor="officer-1", corrected_value="123/A", verdict="agree")
    session.commit()

    assert result["maker_checker_pending"] is False
    refreshed_task = session.get(ReviewTask, task.id)
    assert refreshed_task.closed_at is not None
    refreshed_sample = session.get(AuditSample, sample.id)
    assert refreshed_sample.officer_verdict == "agree"
    assert refreshed_sample.agreed is True


def test_only_the_claimant_can_submit(session, a_page):
    extraction = _extraction(session, a_page)
    task = ReviewTask(extraction_id=extraction.id, reason=None, source_stream="routed")
    session.add(task)
    session.flush()
    task.assignee = "officer-1"
    session.flush()

    with pytest.raises(NotTheClaimant):
        submit(session, task_id=task.id, actor="officer-2", corrected_value="x")


# ---- T3.a indistinguishability ----

def test_routed_and_audit_tasks_serialize_byte_identical_except_ids_and_timestamps(session, a_page):
    routed_extraction = _extraction(session, a_page, field_name="owner_name", raw_value="Ramesh Kumar")
    audit_extraction = _extraction(session, a_page, field_name="owner_name", raw_value="Ramesh Kumar")

    routed = ReviewTask(extraction_id=routed_extraction.id, reason="rule failed: x", source_stream="routed")
    audit = ReviewTask(extraction_id=audit_extraction.id, reason="rule failed: x", source_stream="audit")
    session.add_all([routed, audit])
    session.flush()

    routed_view = strip_review_task_internals(routed).model_dump()
    audit_view = strip_review_task_internals(audit).model_dump()

    # Fields that are legitimately per-instance (ids, the extraction they
    # point at, timestamps) are excluded before the diff; everything else
    # — crucially, no `source_stream` key exists on either view at all —
    # must be byte-identical.
    exempt = {"id", "extraction_id", "opened_at", "closed_at"}
    for key in routed_view:
        if key in exempt:
            continue
        assert routed_view[key] == audit_view[key], f"{key} differs between routed and audit views"
    assert "source_stream" not in routed_view
    assert "source_stream" not in audit_view
