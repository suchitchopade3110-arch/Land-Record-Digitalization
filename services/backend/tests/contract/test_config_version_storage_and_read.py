"""P5-01/02/03 — ConfigVersion storage, immutability, the two-person rule,
and the read API (contracts/openapi/config-service.suchit.yaml §4.1).

Same fixture convention as test_conflict_register_workflow.py and
test_phase4_publication_provenance_audit.py: real Postgres via
TEST_DATABASE_URL, skips if 0006 isn't migrated.

T5.b (two-person rule, DB-level, ORM bypassed): `test_author_equals_approver_rejected_raw_sql`.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from backend.domain.config_versions import (
    ConfigNotFound,
    as_response,
    get_effective_config,
    get_pinned_config,
)
from backend.models.entities import ConfigVersion
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM config_version LIMIT 0"))
            # 0006 adds this constraint; its absence means migrations are
            # behind, not that config_version itself is missing.
            exists = c.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = 'uq_config_version_scope_key_effective_from'")
            ).scalar()
            if not exists:
                pytest.skip(f"{TEST_DB_URL} has no Phase 5 (0006) migrated schema — run migrations first")
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


def _mk(scope="district", key="unit_table.sitapur", value=None, effective_from=None, author="alice", approver="bob"):
    return ConfigVersion(
        scope=scope,
        key=key,
        value=value if value is not None else {"bigha_to_sqm": 1008.0},
        effective_from=effective_from or datetime.now(timezone.utc) - timedelta(days=1),
        author=author,
        approver=approver,
    )


# ---------------------------------------------------------------------------
# T5.b — two-person rule enforced at the DB layer, ORM bypassed.
# ---------------------------------------------------------------------------


def test_author_equals_approver_rejected_raw_sql(engine):
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO config_version (id, scope, key, value, effective_from, author, approver)
                        VALUES (:id, 'district', 'unit_table.raw_sql_test', '{"x": 1}'::jsonb, now(), 'same_person', 'same_person')
                        """
                    ),
                    {"id": "raw-sql-test-id-1"},
                )
        finally:
            trans.rollback()


# ---------------------------------------------------------------------------
# P5-01 — (scope, key, effective_from) uniqueness.
# ---------------------------------------------------------------------------


def test_duplicate_scope_key_effective_from_rejected(session):
    ts = datetime.now(timezone.utc) - timedelta(hours=1)
    session.add(_mk(key="unit_table.dup_test", effective_from=ts, author="alice", approver="bob"))
    session.flush()
    session.add(_mk(key="unit_table.dup_test", effective_from=ts, author="carol", approver="dave"))
    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------------------
# P5-01 — immutable after write, except superseded_by.
# ---------------------------------------------------------------------------


def test_update_to_value_rejected(session):
    row = _mk(key="unit_table.immutable_test")
    session.add(row)
    session.flush()
    row_id = row.id
    session.commit()

    with pytest.raises(ProgrammingError, match="config_version is immutable"):
        session.execute(
            text("UPDATE config_version SET value = :v WHERE id = :id"),
            {"v": '{"bigha_to_sqm": 9999.0}', "id": row_id},
        )
    session.rollback()


def test_update_to_superseded_by_allowed(session, engine):
    old = _mk(key="unit_table.supersede_test", effective_from=datetime.now(timezone.utc) - timedelta(days=2))
    new = _mk(key="unit_table.supersede_test", effective_from=datetime.now(timezone.utc) - timedelta(days=1))
    session.add_all([old, new])
    session.flush()
    old_id, new_id = old.id, new.id
    session.commit()

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE config_version SET superseded_by = :new_id WHERE id = :old_id"),
            {"new_id": new_id, "old_id": old_id},
        )

    with engine.connect() as conn:
        got = conn.execute(
            text("SELECT superseded_by FROM config_version WHERE id = :id"), {"id": old_id}
        ).scalar_one()
    assert got == new_id


# ---------------------------------------------------------------------------
# P5-03 — read API domain functions: unpinned ("effective now") and pinned.
# ---------------------------------------------------------------------------


def test_get_effective_config_returns_latest_effective_row(session):
    key = "unit_table.effective_test"
    older = _mk(key=key, value={"bigha_to_sqm": 1000.0}, effective_from=datetime.now(timezone.utc) - timedelta(days=10))
    newer = _mk(key=key, value={"bigha_to_sqm": 1008.0}, effective_from=datetime.now(timezone.utc) - timedelta(days=1))
    future = _mk(key=key, value={"bigha_to_sqm": 1200.0}, effective_from=datetime.now(timezone.utc) + timedelta(days=1))
    session.add_all([older, newer, future])
    session.flush()
    session.commit()

    row = get_effective_config(session, "district", key)
    assert row.value == {"bigha_to_sqm": 1008.0}
    assert row.id == newer.id


def test_get_effective_config_missing_raises(session):
    with pytest.raises(ConfigNotFound):
        get_effective_config(session, "district", "unit_table.does_not_exist_anywhere")


def test_pinned_read_ignores_newer_effective_version(session):
    key = "unit_table.pinned_test"
    v1 = _mk(key=key, value={"bigha_to_sqm": 1000.0}, effective_from=datetime.now(timezone.utc) - timedelta(days=10))
    session.add(v1)
    session.flush()
    v1_id = v1.id
    session.commit()

    v2 = _mk(key=key, value={"bigha_to_sqm": 1008.0}, effective_from=datetime.now(timezone.utc) - timedelta(days=1))
    session.add(v2)
    session.flush()
    session.commit()

    # Unpinned read now sees v2 ("current" has moved on)...
    assert get_effective_config(session, "district", key).value == {"bigha_to_sqm": 1008.0}
    # ...but the pinned read of v1 is unaffected by v2's existence.
    pinned = get_pinned_config(session, "district", key, v1_id)
    assert pinned.value == {"bigha_to_sqm": 1000.0}
    assert pinned.id == v1_id


def test_pinned_read_wrong_scope_or_key_raises(session):
    row = _mk(key="unit_table.pin_scope_test")
    session.add(row)
    session.flush()
    row_id = row.id
    session.commit()

    with pytest.raises(ConfigNotFound):
        get_pinned_config(session, "state", "unit_table.pin_scope_test", row_id)
    with pytest.raises(ConfigNotFound):
        get_pinned_config(session, "district", "unit_table.wrong_key", row_id)
    with pytest.raises(ConfigNotFound):
        get_pinned_config(session, "district", "unit_table.pin_scope_test", "not-a-real-id")


def test_as_response_shape_matches_contract():
    row = _mk(key="unit_table.shape_test")
    row.id = "fixed-id-for-shape-test"
    row.effective_from = datetime(2026, 1, 1, tzinfo=timezone.utc)
    resp = as_response(row)
    assert set(resp.keys()) == {"key", "value", "config_version", "effective_from"}
    assert resp["config_version"] == "fixed-id-for-shape-test"
    assert resp["key"] == "unit_table.shape_test"
    assert resp["effective_from"] == "2026-01-01T00:00:00+00:00"
