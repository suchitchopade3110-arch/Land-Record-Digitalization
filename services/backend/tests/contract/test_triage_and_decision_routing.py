"""Integration-level tests (real local Postgres — TEST_DATABASE_URL,
defaulting to landrecords_test) for Suchit's own P0 logic: triage routing
(FR-TRI-05/09) and the five-outcome decision engine (FR-CNF-04, FR-CFL-01).
Housed under tests/contract/ alongside this service's other cross-cutting
tests rather than inventing a new top-level category for one file.
"""
import os

import pytest
from backend.domain.decision import UnroutableExtraction, route
from backend.domain.triage import lane_for, route_page
from backend.models.entities import (
    AuditSample,
    Batch,
    Conflict,
    Extraction,
    OperationalAlert,
    Page,
    ReviewTask,
    SourceDocument,
)
from landoutbox.models import OutboxMessage
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
def a_page(session):
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256="b" * 64, storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0)
    session.add(page)
    session.flush()
    return page


# ---- lane_for (pure function, FR-TRI-05) ----

@pytest.mark.parametrize(
    "doc_type,page_role,expected",
    [
        ("jamabandi", "text", ["TEXT_QUEUE"]),
        ("jamabandi", "tabular_register", ["TEXT_QUEUE"]),
        ("cadastral_map", "map_sheet", ["MAP_QUEUE"]),
        ("fmb_sketch", "map_sheet", ["MAP_QUEUE"]),
        ("jamabandi", "blank", []),
        # a page carrying a register table AND a sketch — the fork the PRD
        # names explicitly, both lanes:
        ("cadastral_map", "tabular_register", ["TEXT_QUEUE", "MAP_QUEUE"]),
    ],
)
def test_lane_for_routes_per_FR_TRI_05(doc_type, page_role, expected):
    assert lane_for(doc_type=doc_type, page_role=page_role) == expected


# ---- route_page (pin + outbox, FR-TRI-09) ----

def test_route_page_pins_one_envelope_and_queues_the_text_lane(session, a_page):
    envelope, queued_to = route_page(
        session,
        document_id=a_page.document_id,
        page_id=a_page.id,
        doc_type="jamabandi",
        page_role="text",
        config_version="cfg-v1",
    )
    session.commit()

    assert queued_to == ["TEXT_QUEUE"]
    assert envelope.document_id == a_page.document_id
    assert envelope.page_id == a_page.id
    assert set(envelope.model_versions) == {
        "triage_classifier", "printed_ocr", "hwr", "confidence_calibrator", "novelty_detector",
    }

    outbox_row = session.query(OutboxMessage).filter_by(queue="TEXT_QUEUE").order_by(OutboxMessage.created_at.desc()).first()
    assert outbox_row is not None
    assert outbox_row.envelope["work_envelope"]["envelope_id"] == envelope.envelope_id
    assert outbox_row.envelope["trace_id"] == f"{a_page.document_id}:{a_page.id}"


def test_route_page_queues_both_lanes_for_a_mixed_page(session, a_page):
    _, queued_to = route_page(
        session,
        document_id=a_page.document_id,
        page_id=a_page.id,
        doc_type="cadastral_map",
        page_role="tabular_register",
        config_version="cfg-v1",
    )
    session.commit()

    assert queued_to == ["TEXT_QUEUE", "MAP_QUEUE"]
    outbox_rows = session.query(OutboxMessage).filter(OutboxMessage.queue.in_(["TEXT_QUEUE", "MAP_QUEUE"])).all()
    assert {r.queue for r in outbox_rows} >= {"TEXT_QUEUE", "MAP_QUEUE"}


def test_route_page_and_outbox_write_commit_atomically(session, a_page):
    """ADR-005: if the transaction never commits, neither the envelope
    nor the outbox row exists. Compares against a before/after count
    (rather than asserting a bare zero) because other tests in this
    module commit their own TEXT_QUEUE rows to the same database and
    this test must not depend on running in isolation."""
    outbox_count_before = session.query(OutboxMessage).filter_by(queue="TEXT_QUEUE").count()

    route_page(
        session, document_id=a_page.document_id, page_id=a_page.id,
        doc_type="jamabandi", page_role="text", config_version="cfg-v1",
    )
    session.rollback()

    from landenvelope.models import WorkEnvelope

    assert session.query(WorkEnvelope).filter_by(page_id=a_page.id).count() == 0
    assert session.query(OutboxMessage).filter_by(queue="TEXT_QUEUE").count() == outbox_count_before


# ---- decision.route (the five outcomes, FR-CNF-04/FR-CFL-01) ----

def _extraction(session, page, routing_outcome, calibrated_confidence=0.9):
    e = Extraction(
        page_id=page.id, field_name="owner_name", raw_value="x",
        routing_outcome=routing_outcome, calibrated_confidence=calibrated_confidence,
    )
    session.add(e)
    session.flush()
    return e


def test_auto_accept_creates_no_review_task(session, a_page):
    e = _extraction(session, a_page, "auto_accept")
    result = route(session, e.id)
    session.commit()

    assert result["outcome"] == "auto_accept"
    assert session.query(ReviewTask).filter_by(extraction_id=e.id).count() == 0


def test_audit_sample_creates_an_indistinguishable_review_task_plus_an_audit_sample_row(session, a_page):
    e = _extraction(session, a_page, "audit_sample")
    result = route(session, e.id)
    session.commit()

    task = session.get(ReviewTask, result["review_task_id"])
    assert task.source_stream == "audit"
    sample = session.get(AuditSample, result["audit_sample_id"])
    assert sample.extraction_id == e.id
    assert sample.review_task_id == task.id


def test_review_creates_a_routed_review_task(session, a_page):
    e = _extraction(session, a_page, "review")
    result = route(session, e.id)
    session.commit()

    task = session.get(ReviewTask, result["review_task_id"])
    assert task.source_stream == "routed"


def test_conflict_opens_a_conflict_register_entry(session, a_page):
    e = _extraction(session, a_page, "conflict")
    result = route(session, e.id)
    session.commit()

    conflict = session.get(Conflict, result["conflict_id"])
    assert conflict.origin == "validator"
    assert e.id in conflict.records


def test_outside_calibrated_regime_creates_no_review_task_per_field(session, a_page):
    """FR-CNF-14: never auto-accepted at any confidence, and does NOT
    create a stream of per-field review tasks."""
    e = _extraction(session, a_page, "outside_calibrated_regime")
    result = route(session, e.id)
    session.commit()

    assert result["outcome"] == "outside_calibrated_regime"
    assert session.query(ReviewTask).filter_by(extraction_id=e.id).count() == 0


# ---- T3.e: unknown routing_outcome dead-letters, cluster dedup (P3-01/02) ----

def test_unrecognised_routing_outcome_raises_never_defaults_to_review(session, a_page):
    # `ck_extraction_routing_outcome_enum` already refuses this value at
    # the DB layer (belt and braces, same as every other invariant in this
    # repo) — a genuinely unrecognised value can only exist as an
    # in-session mutation, not a committed row, which is exactly what this
    # test exercises: `route()`'s own guard, not the DB constraint's.
    e = _extraction(session, a_page, "auto_accept")
    e.routing_outcome = "not_a_real_outcome"

    # `route()`'s own guard runs before any autoflush would hand this
    # in-memory-only value to the DB constraint — `no_autoflush` keeps
    # this test isolated to that guard rather than incidentally re-proving
    # the CHECK constraint (already covered: the object is already in the
    # identity map, so `session.get` inside `route` needs no DB roundtrip).
    with session.no_autoflush, pytest.raises(UnroutableExtraction):
        route(session, e.id)
    # Never let the in-memory-only invalid value reach the DB at all —
    # revert it before the next query, rather than relying on every
    # subsequent statement in this test staying inside `no_autoflush`.
    session.expunge(e)
    # never silently created a review task under the "default to review" behavior the brief forbids
    assert session.query(ReviewTask).filter_by(extraction_id=e.id).count() == 0


def test_outside_calibrated_regime_cluster_of_n_raises_one_alert_not_n_tasks(session, a_page):
    import uuid

    cluster_key = f"cluster-{uuid.uuid4()}"  # unique per run — this suite shares a DB across test modules/runs
    extractions = [_extraction(session, a_page, "outside_calibrated_regime") for _ in range(4)]
    results = [route(session, e.id, novelty_cluster_id=cluster_key) for e in extractions]
    session.commit()

    alert_ids = {r["alert_id"] for r in results}
    assert len(alert_ids) == 1  # one alert for the whole cluster
    assert results[0]["deduplicated"] is False
    assert all(r["deduplicated"] for r in results[1:])

    alert = session.get(OperationalAlert, alert_ids.pop())
    assert set(alert.extraction_ids) == {e.id for e in extractions}
    assert session.query(ReviewTask).filter(ReviewTask.extraction_id.in_([e.id for e in extractions])).count() == 0


def test_two_different_clusters_raise_two_separate_alerts(session, a_page):
    e1 = _extraction(session, a_page, "outside_calibrated_regime")
    e2 = _extraction(session, a_page, "outside_calibrated_regime")

    r1 = route(session, e1.id, novelty_cluster_id="cluster-a")
    r2 = route(session, e2.id, novelty_cluster_id="cluster-b")
    session.commit()

    assert r1["alert_id"] != r2["alert_id"]


def test_every_routing_decision_writes_an_audit_event(session, a_page):
    from landaudit.chain import shard_for
    from landaudit.models import AuditEntry

    e = _extraction(session, a_page, "auto_accept")
    route(session, e.id)
    session.commit()

    entry = session.query(AuditEntry).filter_by(shard_id=shard_for(e.id), subject=e.id).one()
    assert entry.action == "decision.route.auto_accept"
