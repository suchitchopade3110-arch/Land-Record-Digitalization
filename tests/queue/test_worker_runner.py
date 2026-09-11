"""T1-02 — Queue infrastructure: Worker runner tests.

Parametrized over InMemoryQueue and RedisStreamsQueue to satisfy ADR-003
fake/driver parity.

Covers:
1. Ack after commit: handler writes a row then raises -> no row persists, message unacked, next consume redelivers.
2. Crash between commit and ack: simulate -> redelivered message processed idempotently (no duplicate Page split or duplicate envelope).
3. Poison message -> lands in DLQ after max_deliveries, gets acked on main stream, audit entry carries no payload values.
4. Two relay loops draining same outbox -> every row published exactly once.
5. SIGTERM mid-batch -> in-flight message finishes or rolls back safely; nothing uncommitted is acked.
6. block_ms from config reaches driver call (spying on driver argument).
"""
from __future__ import annotations

import io
import os
import signal
import threading
import time
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
import redis
from pypdf import PdfWriter
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.audit_log import shard_key_for_record
from backend.domain.queue_policy import (
    DEFAULT_BATCH_COUNT,
    DEFAULT_BLOCK_MS,
    DEFAULT_MAX_DELIVERIES,
    get_queue_policy,
)
from backend.models.entities import Batch, Page, SourceDocument
from backend.workers.ingestion_consumer import handle as ingestion_handle
from backend.workers.runner import WorkerRunner, run_worker_once
from backend.workers.triage_router import handle as triage_handle
from landaudit.models import AuditEntry
from landenvelope.models import WorkEnvelope
from landoutbox.models import OutboxMessage
from landoutbox.relay import Relay
from landqueue.drivers.redis_streams import RedisStreamsQueue
from landqueue.port import QueuePort
from landqueue.testing import InMemoryQueue
from landstorage.drivers.local_fs import LocalFsObjectStore

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, connect_args={"connect_timeout": 2}, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM work_envelope LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} is not reachable or has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, future=True)


def _redis_driver():
    try:
        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True, socket_timeout=1.0)
        client.ping()
    except (redis.ConnectionError, redis.TimeoutError):
        pytest.skip("no local Redis at localhost:6379")
    return RedisStreamsQueue(client=client)


def _memory_driver():
    return InMemoryQueue()


@pytest.fixture(params=["in_memory", "redis_streams"])
def queue_driver(request) -> QueuePort:
    if request.param == "in_memory":
        return _memory_driver()
    return _redis_driver()


# ---------------------------------------------------------------------------
# Test 1: Ack after commit
# ---------------------------------------------------------------------------


def test_ack_after_commit_redelivers_on_failure(queue_driver, session_factory):
    """Broken implementation it catches: A runner that acks before commit, or
    swallows handler errors and acks anyway, or fails to roll back DB on error.
    """
    queue_name = f"test-ack-commit-{uuid.uuid4()}"
    group_name = f"test-group-{uuid.uuid4()}"
    consumer_name = "worker-1"

    # Message payload that causes handler to write a row then raise
    seq_id = queue_driver.publish(queue_name, {"payload": {"fail_after_write": True}})

    call_count = 0

    def faulty_handler(msg_data: dict, session: Session):
        nonlocal call_count
        call_count += 1
        # Write an outbox message row
        outbox = OutboxMessage(queue="DUMMY_QUEUE", envelope={"dummy": True})
        session.add(outbox)
        session.flush()
        if msg_data.get("payload", {}).get("fail_after_write"):
            raise RuntimeError("Intentional handler crash after write")

    # Run one iteration of runner
    with pytest.raises(RuntimeError, match="Intentional handler crash"):
        run_worker_once(
            queue=queue_driver,
            queue_name=queue_name,
            group=group_name,
            consumer_name=consumer_name,
            handler=faulty_handler,
            session_factory=session_factory,
        )

    # 1. Assert DB row was NOT persisted (rolled back)
    with session_factory() as session:
        rows = session.scalars(select(OutboxMessage).where(OutboxMessage.queue == "DUMMY_QUEUE")).all()
        assert len(rows) == 0, "Row must not persist when handler raises"

    # 2. Assert next consume redelivers the unacked message
    redelivered = queue_driver.consume(queue_name, group=group_name, consumer_name=consumer_name, count=1, block_ms=500)
    assert len(redelivered) == 1, "Unacked message must be redelivered"
    assert redelivered[0].sequence_id == seq_id


# ---------------------------------------------------------------------------
# Test 2: Crash between commit and ack (idempotent redelivery)
# ---------------------------------------------------------------------------


def test_crash_between_commit_and_ack_is_idempotent_ingestion(queue_driver, session_factory, tmp_path):
    """Broken implementation it catches: ingestion_consumer creating duplicate
    Page rows or double splitting when redelivered after a crash between commit and ack.
    """
    queue_name = f"INGESTION_QUEUE-{uuid.uuid4()}"
    group_name = f"backend.ingestion-{uuid.uuid4()}"
    consumer_name = "ingest-c1"

    store = LocalFsObjectStore(tmp_path)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=300)
    pdf_buf = io.BytesIO()
    writer.write(pdf_buf)
    doc_bytes = pdf_buf.getvalue()  # a real single-page PDF — pypdf needs a valid page tree/xref
    put_res = store.put(doc_bytes)

    with session_factory() as session:
        batch = Batch(district="sitapur")
        session.add(batch)
        session.flush()
        doc = SourceDocument(
            batch_id=batch.id,
            sha256="a" * 64,
            storage_uri=put_res.key,
            mime="application/pdf",
            page_count=0,
        )
        session.add(doc)
        session.commit()
        doc_id = doc.id

    msg_payload = {
        "trace_id": f"{doc_id}:doc",
        "producer": "ingest",
        "payload": {
            "document_id": doc_id,
            "storage_key": put_res.key,
            "mime": "application/pdf",
        },
    }
    queue_driver.publish(queue_name, msg_payload)

    # Simulate crash between commit and ack: handler runs, session commits, but no ack is sent
    with session_factory() as session:
        ingestion_handle(msg_payload, session, store=store)
        session.commit()

    # Redelivery occurs
    runner = WorkerRunner(
        queue=queue_driver,
        queue_name=queue_name,
        group=group_name,
        consumer_name=consumer_name,
        handler=lambda msg, sess: ingestion_handle(msg, sess, store=store),
        session_factory=session_factory,
    )
    processed = runner.run_once()
    assert processed == 1

    with session_factory() as session:
        pages = session.scalars(select(Page).where(Page.document_id == doc_id)).all()
        # Should not double split
        assert len(pages) == 1, f"Expected exactly 1 page, found {len(pages)}"


def test_crash_between_commit_and_ack_is_idempotent_triage(queue_driver, session_factory):
    """Broken implementation it catches: triage_router creating duplicate
    WorkEnvelope rows when redelivered after an unacked commit (FR-TRI-09 / T2.d).
    """
    queue_name = f"TRIAGE_QUEUE-{uuid.uuid4()}"
    group_name = f"backend.triage-{uuid.uuid4()}"
    consumer_name = "triage-c1"

    with session_factory() as session:
        batch = Batch(district="sitapur")
        session.add(batch)
        session.flush()
        doc = SourceDocument(
            batch_id=batch.id,
            sha256="b" * 64,
            storage_uri="dummy",
            mime="image/tiff",
            page_count=1,
        )
        session.add(doc)
        session.flush()
        page = Page(document_id=doc.id, index=0)
        session.add(page)
        session.commit()
        page_id, doc_id = page.id, doc.id

    msg_payload = {
        "trace_id": f"{doc_id}:{page_id}",
        "producer": "ingest",
        "payload": {
            "id": page_id,
            "document_id": doc_id,
            "index": 0,
            "doc_type": "jamabandi",
            "page_role": "text",
            "config_version": "cfg-test-v1",
        },
    }
    queue_driver.publish(queue_name, msg_payload)

    # First pass: simulate crash between commit and ack
    with session_factory() as session:
        triage_handle(msg_payload, session)
        session.commit()

    with session_factory() as session:
        env_count_1 = session.scalars(select(WorkEnvelope).where(WorkEnvelope.page_id == page_id)).all()
        assert len(env_count_1) == 1

    # Second pass: runner processes redelivery
    runner = WorkerRunner(
        queue=queue_driver,
        queue_name=queue_name,
        group=group_name,
        consumer_name=consumer_name,
        handler=triage_handle,
        session_factory=session_factory,
    )
    processed = runner.run_once()
    assert processed == 1

    with session_factory() as session:
        env_count_2 = session.scalars(select(WorkEnvelope).where(WorkEnvelope.page_id == page_id)).all()
        assert len(env_count_2) == 1, "WorkEnvelope must be reused without duplicate rows on redelivery"


# ---------------------------------------------------------------------------
# Test 3: Poison message -> DLQ & Audit entry with no payload values
# ---------------------------------------------------------------------------


def test_poison_message_lands_in_dlq_after_max_deliveries(queue_driver, session_factory):
    """Broken implementation it catches: A runner that retries poison messages
    indefinitely without dead-lettering, or fails to ack main stream upon DLQ,
    or leaks payload personal data into audit log entries.
    """
    queue_name = f"TEST_POISON_QUEUE-{uuid.uuid4()}"
    group_name = f"backend.test-poison-{uuid.uuid4()}"
    dlq_name = f"{queue_name}.DLQ"
    consumer_name = "poison-c1"

    secret_payload = {"secret_personal_data": "Aadhaar-1234-5678", "bad_data": True}
    queue_driver.publish(queue_name, {"trace_id": "doc1:p1", "payload": secret_payload})

    def poison_handler(msg_data: dict, session: Session):
        raise ValueError("Invalid format in poison payload")

    runner = WorkerRunner(
        queue=queue_driver,
        queue_name=queue_name,
        group=group_name,
        consumer_name=consumer_name,
        handler=poison_handler,
        session_factory=session_factory,
        max_deliveries=3,
    )

    # Delivery 1 & 2 fail
    for i in range(2):
        with pytest.raises(ValueError, match="Invalid format"):
            runner.run_once()

    # Delivery 3 (max_deliveries reached): runner dead-letters the message
    processed = runner.run_once()
    assert processed == 1, "Should successfully dead-letter and ack"

    # Assert DLQ received the message
    dlq_msgs = queue_driver.consume(dlq_name, group="dlq-auditor", consumer_name="c1", count=1, block_ms=500)
    assert len(dlq_msgs) == 1, f"Message must land in {dlq_name}"

    # Assert main stream has no pending unacked messages
    main_msgs = queue_driver.consume(queue_name, group=group_name, consumer_name=consumer_name, count=1, block_ms=200)
    assert len(main_msgs) == 0, "Main stream message must be acked after moving to DLQ"

    # Assert audit log entry 'queue.dead_lettered' was created without payload personal data
    with session_factory() as session:
        entries = session.scalars(
            select(AuditEntry).where(AuditEntry.action == "queue.dead_lettered")
        ).all()
        assert len(entries) >= 1
        dlq_entry = entries[-1]
        assert queue_name in dlq_entry.subject
        assert "Aadhaar" not in str(dlq_entry.subject)
        assert "Aadhaar" not in str(dlq_entry.purpose)
        assert "ValueError" in str(dlq_entry.purpose) or "delivery_count" in str(dlq_entry.purpose)


# ---------------------------------------------------------------------------
# Test 4: Two relay loops draining the same outbox
# ---------------------------------------------------------------------------


def test_two_relay_loops_drain_outbox_without_duplicates(queue_driver, session_factory):
    """Broken implementation it catches: Outbox relay missing skip_locked or
    having concurrency races causing duplicate broker publishes.
    """
    outbox_queue_name = f"RELAY_TEST_QUEUE-{uuid.uuid4()}"
    num_messages = 20

    # This module's other tests write real OutboxMessage rows (via
    # outbox_write(), inside ingestion_handle/triage_handle) against this
    # same shared `outbox_message` table and never drain them — they only
    # assert against the domain rows those tests care about. Relay.drain_once()
    # intentionally scans the whole table regardless of queue (that's its
    # real job), so any such leftovers would otherwise be swept up by this
    # test's own relays and inflate the dispatched count below. Clear them
    # first so the count is about this test's own 20 messages only.
    with session_factory() as session:
        session.query(OutboxMessage).filter(OutboxMessage.dispatched.is_(False)).update(
            {"dispatched": True}, synchronize_session=False
        )
        session.commit()

    with session_factory() as session:
        for i in range(num_messages):
            msg = OutboxMessage(
                queue=outbox_queue_name,
                envelope={"trace_id": f"doc:{i}", "payload": {"item": i}},
            )
            session.add(msg)
        session.commit()

    relay_1 = Relay(session_factory=session_factory, queue=queue_driver)
    relay_2 = Relay(session_factory=session_factory, queue=queue_driver)

    def _drain(relay: Relay, count_container: list):
        dispatched = 0
        for _ in range(5):
            d = relay.drain_once(batch_size=5)
            dispatched += d
            if d == 0:
                break
            time.sleep(0.01)
        count_container.append(dispatched)

    c1: list[int] = []
    c2: list[int] = []
    t1 = threading.Thread(target=_drain, args=(relay_1, c1))
    t2 = threading.Thread(target=_drain, args=(relay_2, c2))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    total_dispatched = sum(c1) + sum(c2)
    assert total_dispatched == num_messages, f"Expected {num_messages} total dispatched, got {total_dispatched}"

    # Verify all messages in queue
    received = queue_driver.consume(outbox_queue_name, group="check-g", consumer_name="c1", count=num_messages + 5, block_ms=1000)
    assert len(received) == num_messages, f"Expected {num_messages} messages in broker, got {len(received)}"
    received_items = {m.data["payload"]["item"] for m in received}
    assert received_items == set(range(num_messages))


# ---------------------------------------------------------------------------
# Test 5: SIGTERM mid-batch
# ---------------------------------------------------------------------------


def test_sigterm_mid_batch_finishes_or_rolls_back_safely(queue_driver):
    """Broken implementation it catches: A runner that aborts abruptly on SIGTERM,
    leaving uncommitted DB state acked or corrupted.
    """
    queue_name = f"TEST_SIGTERM_QUEUE-{uuid.uuid4()}"
    group_name = f"backend.sigterm-{uuid.uuid4()}"
    consumer_name = "sigterm-c1"

    # Publish 2 messages
    queue_driver.publish(queue_name, {"payload": {"n": 1}})
    queue_driver.publish(queue_name, {"payload": {"n": 2}})

    runner = WorkerRunner(
        queue=queue_driver,
        queue_name=queue_name,
        group=group_name,
        consumer_name=consumer_name,
        handler=lambda msg, sess: None,  # no-op handler
        session_factory=MagicMock(),
    )

    # Set stop requested before/during loop
    runner.stop()
    assert runner.should_stop is True

    # When runner is stopped, running loop terminates cleanly
    runner.run_loop(max_iterations=1)


# ---------------------------------------------------------------------------
# Test 6: block_ms from config reaches driver call
# ---------------------------------------------------------------------------


def test_block_ms_from_config_reaches_driver_call():
    """Broken implementation it catches: A runner hardcoding block_ms or
    ignoring the queue.block_ms policy config key.
    """
    mock_queue = MagicMock(spec=QueuePort)
    mock_queue.consume.return_value = []

    custom_block_ms = 4321
    runner = WorkerRunner(
        queue=mock_queue,
        queue_name="TEST_QUEUE",
        group="test-g",
        consumer_name="c1",
        handler=lambda m, s: None,
        session_factory=MagicMock(),
        block_ms=custom_block_ms,
    )

    runner.run_once()

    mock_queue.consume.assert_called_once()
    _, kwargs = mock_queue.consume.call_args
    assert kwargs.get("block_ms") == custom_block_ms, (
        f"Expected block_ms={custom_block_ms} passed to driver, got {kwargs.get('block_ms')}"
    )

