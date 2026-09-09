"""T3.b (publish block) and P3-10/11/12 — conflict register workflow,
config-driven blocking-state set, and resolved conflicts rejoining the
publish path.
"""
import os

import pytest
from backend.domain.conflict_register import (
    InvalidConflictTransition,
    assign,
    is_publish_blocked,
    list_aged,
    list_conflicts,
    open_conflict,
    transition,
)
from backend.domain.publication_gate import (
    PublishBlocked,
    attempt_publish,
    check_open_conflict,
)
from backend.domain.review_policy import BLOCKING_CONFLICT_STATES
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
            c.execute(text("SELECT 1 FROM conflict LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


def test_referred_blocks_publication_the_config_driven_way(session):
    """P3-11 — the blocking-state set is config
    (`review_policy.BLOCKING_CONFLICT_STATES`), not a hard-coded
    `!= 'resolved'`: `referred` blocks even though it isn't `open` or
    `under_enquiry` either, because it's in that set."""
    assert "referred" in BLOCKING_CONFLICT_STATES
    conflict = open_conflict(session, records=["rec-1"], rule="area_mismatch", evidence={}, origin="validator")
    transition(session, conflict.id, new_state="referred", actor="supervisor-1")
    session.flush()

    assert is_publish_blocked(session, "rec-1") is True


def test_publish_block_names_the_conflict_id_and_rule(session):
    """T3.b — a specific error naming the conflict, per P3-11."""
    conflict = open_conflict(session, records=["rec-2"], rule="lineage: transferor mismatch", evidence={}, origin="validator")
    session.flush()

    with pytest.raises(PublishBlocked) as excinfo:
        attempt_publish(session, "rec-2", [check_open_conflict])

    assert conflict.id in str(excinfo.value)
    assert "lineage: transferor mismatch" in str(excinfo.value)


def test_transition_to_resolved_requires_a_resolution_reason(session):
    conflict = open_conflict(session, records=["rec-3"], rule="area_mismatch", evidence={}, origin="validator")
    session.flush()

    with pytest.raises(InvalidConflictTransition):
        transition(session, conflict.id, new_state="resolved", actor="supervisor-1")


def test_resolved_conflict_rejoins_the_publish_path(session):
    """P3-12."""
    conflict = open_conflict(session, records=["rec-4"], rule="area_mismatch", evidence={}, origin="validator")
    session.flush()
    assert is_publish_blocked(session, "rec-4") is True

    transition(session, conflict.id, new_state="resolved", actor="supervisor-1", resolution="field verified against original register entry")
    session.flush()

    assert is_publish_blocked(session, "rec-4") is False
    attempt_publish(session, "rec-4", [check_open_conflict])  # does not raise


def test_assign_and_list_conflicts_by_state(session):
    conflict = open_conflict(session, records=["rec-5"], rule="area_mismatch", evidence={}, origin="validator")
    session.flush()
    assigned = assign(session, conflict.id, assignee="supervisor-2", actor="admin-1")
    session.flush()

    assert assigned.assignee == "supervisor-2"
    open_conflicts = list_conflicts(session, state="open")
    assert conflict.id in {c.id for c in open_conflicts}


def test_list_aged_only_returns_still_blocking_conflicts(session):
    from datetime import timedelta

    conflict = open_conflict(session, records=["rec-6"], rule="area_mismatch", evidence={}, origin="validator")
    session.flush()

    assert conflict in list_aged(session, older_than=timedelta(seconds=-1))
    transition(session, conflict.id, new_state="resolved", actor="supervisor-1", resolution="resolved")
    session.flush()
    assert conflict not in list_aged(session, older_than=timedelta(seconds=-1))
