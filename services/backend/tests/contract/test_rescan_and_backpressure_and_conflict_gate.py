"""P2-12 (RescanTask lifecycle), P2-13 (backpressure signal), and the
conflict-register caller of the shared publication gate (P2-08's second
caller) — smaller pieces of this phase without a dedicated named T2.x
test, covered together here rather than in three near-empty files.
"""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.domain.backpressure import check_backlog
from backend.domain.conflict_register import is_publish_blocked, open_conflict
from backend.domain.rescan import InvalidRescanTaskTransition, assign, close, list_aged, open_from_threshold_breach
from backend.models.entities import Batch, Page, SourceDocument
from landoutbox.models import OutboxMessage

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
    doc = SourceDocument(batch_id=batch.id, sha256=os.urandom(32).hex(), storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0)
    session.add(page)
    session.flush()
    return page


# ---- RescanTask lifecycle (P2-12, FR-TRI-01) ----


def test_open_from_threshold_breach_creates_an_open_task_with_a_reason_code(session, a_page):
    task = open_from_threshold_breach(session, page_id=a_page.id, reason_code="quality_below_threshold")
    session.flush()

    assert task.state == "open"
    assert task.reason_code == "quality_below_threshold"
    assert task.owner is None


def test_assign_then_close_moves_through_the_lifecycle(session, a_page):
    task = open_from_threshold_breach(session, page_id=a_page.id, reason_code="illegible_region")
    session.flush()

    assigned = assign(session, task.id, owner="record-room-clerk-3")
    session.flush()
    assert assigned.state == "assigned"
    assert assigned.owner == "record-room-clerk-3"

    closed = close(session, task.id, actor="record-room-clerk-3")
    session.flush()
    assert closed.state == "closed"
    assert closed.closed_at is not None


def test_cannot_assign_an_already_closed_task(session, a_page):
    task = open_from_threshold_breach(session, page_id=a_page.id, reason_code="illegible_region")
    close(session, task.id, actor="someone")
    session.flush()

    with pytest.raises(InvalidRescanTaskTransition):
        assign(session, task.id, owner="someone-else")


def test_list_aged_finds_only_tasks_older_than_the_cutoff(session, a_page):
    from datetime import timedelta

    task = open_from_threshold_breach(session, page_id=a_page.id, reason_code="illegible_region")
    session.flush()

    assert task in list_aged(session, older_than=timedelta(seconds=-1))  # "older than the future" == everything open
    assert task not in list_aged(session, older_than=timedelta(days=9999))


# ---- backpressure (P2-13) ----


def test_check_backlog_counts_only_undispatched_rows_for_the_given_queue(session):
    # A fresh, per-run queue name — a bare literal would accumulate rows
    # across repeated runs against a shared (not `make test-db`-reset)
    # Postgres and make this test's own counts drift from what it just wrote.
    queue = f"TEST_BACKLOG_QUEUE-{uuid.uuid4()}"
    session.add(OutboxMessage(queue=queue, envelope={"payload": {}}, dispatched=False))
    session.add(OutboxMessage(queue=queue, envelope={"payload": {}}, dispatched=True))
    session.flush()

    status = check_backlog(session, queue=queue, ceiling=100)
    assert status.undispatched_count == 1
    assert status.over_ceiling is False


def test_check_backlog_flags_over_ceiling_purely_as_a_signal(session):
    queue = f"TEST_BACKLOG_QUEUE-{uuid.uuid4()}"
    for _ in range(3):
        session.add(OutboxMessage(queue=queue, envelope={"payload": {}}, dispatched=False))
    session.flush()

    status = check_backlog(session, queue=queue, ceiling=1)
    assert status.over_ceiling is True
    assert status.undispatched_count == 3


# ---- conflict register as the publication gate's second caller (P2-08) ----


def test_a_record_with_an_open_conflict_is_publish_blocked(session):
    conflict = open_conflict(session, records=["record-1", "record-2"], rule="area_mismatch", evidence={}, origin="validator")
    session.flush()

    assert is_publish_blocked(session, "record-1") is True
    assert is_publish_blocked(session, "record-2") is True
    assert is_publish_blocked(session, "record-not-in-this-conflict") is False


def test_a_resolved_conflict_no_longer_blocks_publication(session):
    conflict = open_conflict(session, records=["record-3"], rule="area_mismatch", evidence={}, origin="validator")
    conflict.state = "resolved"
    session.flush()

    assert is_publish_blocked(session, "record-3") is False
