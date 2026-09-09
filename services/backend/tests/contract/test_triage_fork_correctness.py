"""T2.e — Fork correctness. A page classified `tabular_register` +
`sketch` enters both queues exactly once each. A text-only page never
populates MAP_QUEUE.

`backend.domain.triage.lane_for`'s pure-function cases are already
covered by `test_triage_and_decision_routing.py`; this file closes the
loop at the outbox level — the actual TEXT_QUEUE/MAP_QUEUE rows
`route_page` writes, not just the lane list it computes.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.domain.triage import route_page
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


def _new_page(session, **overrides) -> Page:
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256=os.urandom(32).hex(), storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0, **overrides)
    session.add(page)
    session.flush()
    return page


def test_a_mixed_register_and_sketch_page_enters_both_queues_exactly_once_each(session):
    page = _new_page(session, doc_type="cadastral_map", page_role="tabular_register")

    _, queued_to = route_page(
        session, document_id=page.document_id, page_id=page.id,
        doc_type="cadastral_map", page_role="tabular_register", config_version="cfg-v1",
    )
    session.flush()

    assert queued_to == ["TEXT_QUEUE", "MAP_QUEUE"]
    for queue in ("TEXT_QUEUE", "MAP_QUEUE"):
        rows = [
            r for r in session.query(OutboxMessage).filter_by(queue=queue).all()
            if r.envelope["payload"]["page_id"] == page.id
        ]
        assert len(rows) == 1, f"expected exactly one {queue} message for page {page.id}, got {len(rows)}"


def test_a_text_only_page_never_populates_map_queue(session):
    page = _new_page(session, doc_type="jamabandi", page_role="text")

    _, queued_to = route_page(
        session, document_id=page.document_id, page_id=page.id,
        doc_type="jamabandi", page_role="text", config_version="cfg-v1",
    )
    session.flush()

    assert queued_to == ["TEXT_QUEUE"]
    map_rows = [
        r for r in session.query(OutboxMessage).filter_by(queue="MAP_QUEUE").all()
        if r.envelope["payload"]["page_id"] == page.id
    ]
    assert map_rows == []


def test_a_map_only_page_never_populates_text_queue(session):
    page = _new_page(session, doc_type="cadastral_map", page_role="map_sheet")

    _, queued_to = route_page(
        session, document_id=page.document_id, page_id=page.id,
        doc_type="cadastral_map", page_role="map_sheet", config_version="cfg-v1",
    )
    session.flush()

    assert queued_to == ["MAP_QUEUE"]
    text_rows = [
        r for r in session.query(OutboxMessage).filter_by(queue="TEXT_QUEUE").all()
        if r.envelope["payload"]["page_id"] == page.id
    ]
    assert text_rows == []


def test_a_blank_page_enters_neither_queue(session):
    page = _new_page(session, doc_type="jamabandi", page_role="blank")

    _, queued_to = route_page(
        session, document_id=page.document_id, page_id=page.id,
        doc_type="jamabandi", page_role="blank", config_version="cfg-v1",
    )
    session.flush()

    assert queued_to == []
