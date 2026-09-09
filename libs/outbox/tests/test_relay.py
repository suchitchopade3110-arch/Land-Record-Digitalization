"""Real Postgres + real Redis test — proves ADR-005's two failure windows
are actually closed: "wrote the row, crashed before emitting" is
impossible to observe (the outbox row commits with the domain write), and
"emitted, crashed before commit" is impossible by construction (the relay
only ever publishes already-committed rows)."""
"""Targets its own scratch database (`LIB_TEST_DATABASE_URL`, default
`landrecords_libtest`), not `landrecords_test` — see
libs/audit/tests/test_chain.py's module docstring for why this package's
own create_all/drop_all fixtures must never run against the
Alembic-migrated database other suites depend on staying intact."""
import os
import uuid

import pytest
import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from landoutbox.models import OutboxBase, OutboxMessage
from landoutbox.relay import Relay
from landoutbox.writer import write
from landqueue.drivers.redis_streams import RedisStreamsQueue

TEST_DB_URL = os.environ.get(
    "LIB_TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_libtest"
)


@pytest.fixture
def db():
    try:
        engine = create_engine(TEST_DB_URL, future=True)
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        pytest.skip(f"no local Postgres at {TEST_DB_URL}")
    OutboxBase.metadata.create_all(engine)
    yield engine
    OutboxBase.metadata.drop_all(engine)


@pytest.fixture
def redis_client():
    try:
        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        client.ping()
    except redis.ConnectionError:
        pytest.skip("no local Redis at localhost:6379")
    return client


def test_outbox_row_commits_atomically_with_the_domain_write(db):
    """Models 'crash before emit': the outbox row and the (simulated)
    domain write share one transaction. If the transaction rolls back,
    neither the domain write nor the outbox row exists — there is no
    state where the row committed but the outbox entry didn't."""
    Session_ = sessionmaker(bind=db)

    with Session_() as session:
        write(session, queue="triage-queue", envelope={"trace_id": "doc1:page1", "payload": {"n": 1}})
        session.rollback()  # simulate a crash before commit

    with Session_() as session:
        assert session.query(OutboxMessage).count() == 0


def test_relay_publishes_committed_outbox_rows_and_marks_them_dispatched(db, redis_client):
    stream = f"test-outbox-{uuid.uuid4()}"
    Session_ = sessionmaker(bind=db)

    with Session_() as session:
        write(session, queue=stream, envelope={"trace_id": "doc1:page1", "payload": {"n": 1}})
        session.commit()

    queue = RedisStreamsQueue(client=redis_client)
    relay = Relay(session_factory=Session_, queue=queue)
    dispatched = relay.drain_once()

    assert dispatched == 1
    with Session_() as session:
        row = session.query(OutboxMessage).one()
        assert row.dispatched is True
        assert row.dispatched_at is not None

    delivered = queue.consume(stream, group="verify", consumer_name="c1", count=1, block_ms=500)
    assert len(delivered) == 1
    assert delivered[0].data["payload"]["n"] == 1
    redis_client.delete(stream)


def test_relay_is_a_noop_on_an_already_dispatched_row(db, redis_client):
    """Re-running the relay after a crash mid-batch must not error or
    double-count on rows it already marked dispatched."""
    stream = f"test-outbox-{uuid.uuid4()}"
    Session_ = sessionmaker(bind=db)

    with Session_() as session:
        write(session, queue=stream, envelope={"trace_id": "doc1:page1", "payload": {}})
        session.commit()

    queue = RedisStreamsQueue(client=redis_client)
    relay = Relay(session_factory=Session_, queue=queue)

    first_pass = relay.drain_once()
    second_pass = relay.drain_once()

    assert first_pass == 1
    assert second_pass == 0  # nothing left undispatched
    redis_client.delete(stream)
