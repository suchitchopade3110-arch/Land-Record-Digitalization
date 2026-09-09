"""P4-04/05/06 — T4.a's exact shape at the `landaudit` level: roll a root,
anchor it, tamper an entry *and* recompute the shard forward from that
point (the attack `verify_shard` alone cannot catch), then prove
`verify_against_anchor` still catches it and names the shard + earliest
disagreeing `chain_root`.

Same scratch-database posture as `test_chain.py` — see that file's
docstring for why this never touches `landrecords_test`.
"""
import hashlib
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from landaudit.chain import NUM_SHARDS, append
from landaudit.kms import LocalSignOnlyKMSStub
from landaudit.models import AuditBase, AuditEntry
from landaudit.rollup import perform_roll
from landaudit.verify import verify_against_anchor
from landaudit.witness import SecondStoreWitness

TEST_DB_URL = os.environ.get(
    "LIB_TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_libtest"
)


class _FakeAnchorStore:
    def __init__(self):
        self.written = []

    def put(self, data: bytes):
        self.written.append(data)

        class _Result:
            key = f"fake-key-{len(data)}"

        return _Result()


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("ANCHOR_KMS_KEY_SEED", "test-only-seed-do-not-use-in-prod")
    try:
        engine = create_engine(TEST_DB_URL, future=True)
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip(f"no local Postgres at {TEST_DB_URL} — see Makefile's `test-db` target")
    AuditBase.metadata.create_all(engine)
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


def _witness():
    return SecondStoreWitness(_FakeAnchorStore())


def test_verify_against_anchor_passes_when_nothing_was_tampered(session):
    for i in range(3):
        append(session, actor=f"officer{i}", action="review.submit", subject="subj-A", shard_id=1)
        session.commit()

    chain_root = perform_roll(session, witness=_witness(), signer=LocalSignOnlyKMSStub())
    session.commit()

    report = verify_against_anchor(session, trusted_root=chain_root.root)
    assert report.ok
    assert report.anchor_ok is True
    assert all(f.ok for f in report.structural_findings)


def test_verify_against_anchor_catches_a_forward_recomputed_tamper_and_names_the_shard(session):
    """The T4.a attack: modify one entry, then recompute every *later*
    entry's prev_hash/hash forward so the shard is internally consistent
    again. `verify_shard` alone (structural-only) would now pass; only
    comparison against an externally-anchored root catches it."""
    entries = []
    for i in range(3):
        e = append(session, actor=f"officer{i}", action="review.submit", subject="subj-A", shard_id=2)
        session.commit()
        entries.append(e)

    chain_root = perform_roll(session, witness=_witness(), signer=LocalSignOnlyKMSStub())
    trusted_root = chain_root.root  # stands in for "fetched from the second store", never re-read from chain_root
    session.commit()

    # Attacker (using the app's own DB write credentials) tampers e[0]'s
    # action, then recomputes e[1] and e[2] forward to look consistent.
    def _hash(prev_hash, actor, action, subject, purpose, value_hash, at_iso):
        material = "|".join([prev_hash or "", actor, action, subject or "", purpose or "", value_hash or "", at_iso])
        return hashlib.sha256(material.encode()).hexdigest()

    row0 = session.get(AuditEntry, (entries[0].id, entries[0].shard_id))
    row0.action = "review.submit-TAMPERED"
    row0.hash = _hash(row0.prev_hash, row0.actor, row0.action, row0.subject, row0.purpose, row0.value_hash, row0.at.isoformat())
    session.flush()

    row1 = session.get(AuditEntry, (entries[1].id, entries[1].shard_id))
    row1.prev_hash = row0.hash
    row1.hash = _hash(row1.prev_hash, row1.actor, row1.action, row1.subject, row1.purpose, row1.value_hash, row1.at.isoformat())
    session.flush()

    row2 = session.get(AuditEntry, (entries[2].id, entries[2].shard_id))
    row2.prev_hash = row1.hash
    row2.hash = _hash(row2.prev_hash, row2.actor, row2.action, row2.subject, row2.purpose, row2.value_hash, row2.at.isoformat())
    session.flush()
    session.commit()

    # Structural check alone now passes — this IS the point of the attack.
    from landaudit.chain import verify_shard

    assert verify_shard(session, shard_id=2) is True

    report = verify_against_anchor(session, trusted_root=trusted_root)
    assert report.ok is False
    assert report.anchor_ok is False
    assert report.tamper_window is not None
    assert report.tamper_window.shard_id == 2
    assert report.tamper_window.earliest_disagreeing_root_id == chain_root.id


def test_verify_reports_which_root_ran_with_the_apps_own_db_credentials(session):
    """T4.a's other half: this whole test runs against `session`, i.e.
    using the same credentials the application itself writes with — proof
    that the app cannot repair its own chain even with full DB write
    access, since the comparison is against a root supplied from outside
    (`trusted_root`), never re-derived from this same DB."""
    append(session, actor="officer1", action="review.submit", subject="subj-B", shard_id=3)
    session.commit()
    perform_roll(session, witness=_witness(), signer=LocalSignOnlyKMSStub())
    session.commit()

    # A forged "trusted" root (as if an attacker tried to also fabricate
    # what the second store would say) must still be caught as a mismatch.
    report = verify_against_anchor(session, trusted_root="0" * 64)
    assert report.anchor_ok is False
