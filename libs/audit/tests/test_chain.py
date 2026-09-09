"""Real Postgres test — proves the hash chain detects tampering end to
end, not just that the functions type-check.

Deliberately targets its **own** scratch database
(`LIB_TEST_DATABASE_URL`, default `landrecords_libtest`), separate from
`landrecords_test` (`TEST_DATABASE_URL`), which `tests/invariant/` and
`services/backend/tests/contract/` assume already carries the *migrated*
schema. This fixture's `create_all`/`drop_all` against `AuditBase.metadata`
would otherwise reflect onto — and then `drop_all` would destroy —
`landrecords_test`'s real, Alembic-migrated, hash-partitioned
`audit_entry` table (with its 8 real child partitions) out from under any
suite that runs after this one in the same `make verify` pass. Keeping
this package's own unit-style DB tests on a separate database is what
makes that impossible by construction rather than by test-ordering luck.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from landaudit.chain import NUM_SHARDS, ChainTamperedError, append, verify_shard
from landaudit.models import AuditBase

TEST_DB_URL = os.environ.get(
    "LIB_TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_libtest"
)


@pytest.fixture
def session():
    try:
        engine = create_engine(TEST_DB_URL, future=True)
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip(f"no local Postgres at {TEST_DB_URL} — see Makefile's `test-db` target")
    AuditBase.metadata.create_all(engine)
    # create_all() emits the partitioned parent with no storage of its
    # own (ADR-006). Postgres does not allow a DEFAULT partition on a
    # HASH-partitioned table, so — unlike a RANGE/LIST parent — there is
    # no test-only shortcut: reproduce the same MODULUS/REMAINDER split
    # infra/migrations/versions/0002_core_schema.py creates in production.
    with engine.begin() as c:
        for shard in range(NUM_SHARDS):
            c.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS audit_entry_p{shard} PARTITION OF audit_entry "
                    f"FOR VALUES WITH (MODULUS {NUM_SHARDS}, REMAINDER {shard})"
                )
            )
    with Session(engine) as s:
        yield s
    AuditBase.metadata.drop_all(engine)


def test_first_entry_in_a_shard_has_no_prev_hash(session):
    entry = append(session, actor="officer1", action="review.submit", subject="extraction-1")
    session.commit()

    assert entry.prev_hash is None
    assert entry.hash is not None


def test_chain_links_successive_entries_in_the_same_shard(session):
    e1 = append(session, actor="officer1", action="review.submit", subject="subj-A", shard_id=3)
    session.commit()
    e2 = append(session, actor="officer2", action="review.submit", subject="subj-A", shard_id=3)
    session.commit()

    assert e2.prev_hash == e1.hash


def test_verify_shard_passes_on_an_untampered_chain(session):
    for i in range(5):
        append(session, actor=f"officer{i}", action="review.submit", subject="subj-A", shard_id=1)
        session.commit()

    assert verify_shard(session, shard_id=1) is True


def test_verify_shard_detects_tampering_even_when_the_tampered_row_recomputes_its_own_hash(session):
    """The property FR-PUB-03 requires: 'tampering is detectable by
    verifying the chain,' including an attacker who edits one row's value
    and recomputes *that* row's own hash to look self-consistent — caught
    because the *next* row's prev_hash/hash was computed over the
    original value, not the tampered one."""
    import hashlib

    from landaudit.models import AuditEntry

    e1 = append(session, actor="officer1", action="review.submit", subject="subj-A", shard_id=2)
    session.commit()
    append(session, actor="officer2", action="review.submit", subject="subj-A", shard_id=2)
    session.commit()

    assert verify_shard(session, shard_id=2) is True

    # Attacker tampers with e1's action, and recomputes e1's own hash to
    # be internally consistent with the tampered field.
    row = session.get(AuditEntry, (e1.id, e1.shard_id))
    row.action = "review.submit-BUT-SECRETLY-DIFFERENT"
    row.hash = hashlib.sha256(
        f"|{row.actor}|{row.action}|{row.subject}||{row.value_hash or ''}|{row.at.isoformat()}".encode()
    ).hexdigest()
    session.commit()

    with pytest.raises(ChainTamperedError):
        verify_shard(session, shard_id=2)


def test_different_subjects_can_land_in_different_shards(session):
    e1 = append(session, actor="a", action="x", subject="alpha")
    e2 = append(session, actor="a", action="x", subject="totally-different-subject-key")
    session.commit()

    # Not asserting they're always different (hash collisions into the
    # same shard are expected and fine) — asserting shard assignment is
    # deterministic and reproducible from the subject alone.
    from landaudit.chain import shard_for

    assert e1.shard_id == shard_for("alpha")
    assert e2.shard_id == shard_for("totally-different-subject-key")


def test_value_is_never_stored_only_its_hash_FR_SEC_08(session):
    import hashlib

    real_value = "Ram Prasad"
    append(
        session,
        actor="auditor1",
        action="field.unmasked_read",
        subject="extraction-1",
        purpose="dispute investigation case #42",
        value_hash=hashlib.sha256(real_value.encode()).hexdigest(),
    )
    session.commit()

    from landaudit.models import AuditEntry

    row = session.query(AuditEntry).filter_by(subject="extraction-1").one()
    assert real_value not in (row.value_hash or "")
    assert row.purpose == "dispute investigation case #42"
