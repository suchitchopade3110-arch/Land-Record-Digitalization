"""T2.b — Completeness. A batch with three pages withheld raises an alert
naming the missing index positions, and a publish attempt from that
volume fails with the gap named in the failure.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.domain.completeness import acknowledge_gap, current_alert, record_index_position, rebuild
from backend.domain.publication_gate import PublishBlocked, attempt_publish, check_volume_completeness
from backend.models.entities import Batch, Page, SourceDocument

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
def a_batch_with_pages(session):
    """A 10-page register (index_position 1..10) with positions 4, 5, 6
    withheld — the batch T2.b's "three pages withheld" describes."""
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256="f" * 64, storage_uri="x", mime="application/pdf", page_count=7)
    session.add(doc)
    session.flush()

    present_positions = [1, 2, 3, 7, 8, 9, 10]
    for i, pos in enumerate(present_positions):
        page = Page(document_id=doc.id, index=i)
        session.add(page)
        session.flush()
        record_index_position(session, page_id=page.id, index_position=pos)
    session.flush()
    return batch


def test_a_batch_with_withheld_pages_raises_an_alert_naming_the_missing_positions(session, a_batch_with_pages):
    volume_index = rebuild(session, a_batch_with_pages.id)
    session.flush()

    assert volume_index.gaps == [4, 5, 6]
    assert volume_index.state == "gap_detected"

    alert = current_alert(session, a_batch_with_pages.id)
    assert alert is not None
    assert alert.missing_index_positions == [4, 5, 6]


def test_a_complete_batch_has_no_alert_and_state_complete(session):
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256="1" * 64, storage_uri="x", mime="application/pdf", page_count=3)
    session.add(doc)
    session.flush()
    for i, pos in enumerate([1, 2, 3]):
        page = Page(document_id=doc.id, index=i)
        session.add(page)
        session.flush()
        record_index_position(session, page_id=page.id, index_position=pos)

    volume_index = rebuild(session, batch.id)
    session.flush()

    assert volume_index.gaps == []
    assert volume_index.state == "complete"
    assert current_alert(session, batch.id) is None


def test_a_publish_attempt_from_a_batch_with_an_open_gap_fails_naming_the_gap(session, a_batch_with_pages):
    rebuild(session, a_batch_with_pages.id)
    session.flush()

    with pytest.raises(PublishBlocked) as exc_info:
        attempt_publish(session, a_batch_with_pages.id, checks=[check_volume_completeness])

    reason = exc_info.value.reasons[0]
    assert reason.code == "volume_gap"
    assert reason.detail["missing_index_positions"] == [4, 5, 6]
    assert reason.detail["batch_id"] == a_batch_with_pages.id


def test_publish_is_unblocked_once_an_operator_acknowledges_the_gap(session, a_batch_with_pages):
    rebuild(session, a_batch_with_pages.id)
    session.flush()

    acknowledge_gap(session, a_batch_with_pages.id, actor="operator:jane", reason_code="pages_lost_in_original_binding")
    session.flush()

    # attempt_publish no longer raises — the gap is acknowledged, not
    # resolved (the pages may genuinely never arrive), but publication is
    # unblocked per FR-ING-08.
    result = attempt_publish(session, a_batch_with_pages.id, checks=[check_volume_completeness])
    assert result.blocked is False


def test_publish_succeeds_for_a_batch_with_no_volume_index_at_all(session):
    """A batch nobody has ever rebuilt a VolumeIndex for (e.g. no
    index_position data has arrived yet) must not be blocked by a check
    that has nothing to report — absence of data is not itself a gap."""
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()

    result = attempt_publish(session, batch.id, checks=[check_volume_completeness])
    assert result.blocked is False
