"""Phase 1 gate: a fake end-to-end run from upload to a routing decision,
over the *real* infrastructure (Postgres, Redis Streams, the transactional
outbox) with schema-valid fake output standing in for every stage not
owned by Suchit — per Rule 0's "stub worker" contract: reads the real
contract, emits schema-valid fake output, replaceable with zero caller
changes once Shree/Shruthi/Tharun's real implementations land.

Path exercised: ingest (fake) → triage routing (real, `backend.domain.triage`)
→ transactional outbox → real Redis Streams TEXT_QUEUE → a stub text-lane
consumer (stands in for Shree's M3, and — to keep one test readable —
also stands in for Shruthi's M6/M7 and Tharun's M8, since none of those
are Suchit's code to write for real) → the real five-outcome decision
engine (`backend.domain.decision`, Suchit's own P0 scope).

This is intentionally not a test of Shree/Shruthi/Tharun's logic — it is a
test that the pipeline *plumbing* between four independently-owned stages
actually carries a message end to end without anyone's code needing to
change when the real implementations replace these stubs.
"""
import os
import uuid

import pytest
import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.decision import route
from backend.domain.triage import route_page
from backend.models.entities import Batch, Extraction, Page, SourceDocument
from landoutbox.models import OutboxMessage
from landoutbox.relay import Relay
from landqueue.drivers.redis_streams import RedisStreamsQueue

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM work_envelope LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def redis_client():
    try:
        client = redis.Redis.from_url("redis://localhost:6379/0", decode_responses=True)
        client.ping()
    except redis.ConnectionError:
        pytest.skip("no local Redis at localhost:6379")
    return client


def _stub_text_lane_and_beyond(session: Session, page_id: str, envelope: dict) -> Extraction:
    """Stands in for Shree's OCR/HWR (M3) + normalize (M5) + Shruthi's
    validators (M6) + Tharun's confidence/novelty (M8) in one shortcut, so
    this one test can walk ingest-to-decision without four people's real
    models. Emits a schema-valid `Extraction` — every field this stage
    chain is contractually responsible for populating
    (`contracts/schemas/extraction.schema.json`) gets a value, even though
    the values themselves are fake.
    """
    extraction = Extraction(
        page_id=page_id,
        field_name="owner_name",
        raw_value="राम प्रसाद",
        canonical_value="Ram Prasad",
        engine="printed_ocr",
        model_version=envelope["model_versions"]["printed_ocr"],
        config_version=envelope["config_version"],
        token_confidence=0.97,
        calibrated_confidence=0.95,  # Tharun's stand-in output
        novelty_score=0.02,
        routing_outcome="auto_accept",  # Tharun's stand-in decision
    )
    session.add(extraction)
    session.flush()
    return extraction


def test_fake_end_to_end_run_from_ingest_to_a_routing_decision(engine, redis_client):
    Session_ = sessionmaker(bind=engine)
    queue = RedisStreamsQueue(client=redis_client)
    text_queue_name = f"TEXT_QUEUE-e2e-{uuid.uuid4()}"

    # --- ingest (fake): a batch, a source document, one page ---
    with Session_() as session:
        batch = Batch(district="sitapur")
        session.add(batch)
        session.flush()
        doc = SourceDocument(
            batch_id=batch.id, sha256="d" * 64, storage_uri="sha256/dd/dd/" + "d" * 64,
            mime="image/tiff", page_count=1,
        )
        session.add(doc)
        session.flush()
        page = Page(document_id=doc.id, index=0, doc_type="jamabandi", page_role="text")
        session.add(page)
        session.commit()
        page_id, document_id = page.id, doc.id

    # --- triage routing (real): pin the envelope, queue the text lane ---
    with Session_() as session:
        envelope, queued_to = route_page(
            session,
            document_id=document_id,
            page_id=page_id,
            doc_type="jamabandi",
            page_role="text",
            config_version="cfg-e2e-v1",
        )
        # Route directly to our test-isolated queue name rather than the
        # real TEXT_QUEUE, so this test doesn't collide with any other
        # suite's traffic on a shared broker.
        for row in session.query(OutboxMessage).filter(OutboxMessage.queue == "TEXT_QUEUE").order_by(
            OutboxMessage.created_at.desc()
        ).limit(1):
            row.queue = text_queue_name
        session.commit()
        envelope_id = envelope.envelope_id

    assert queued_to == ["TEXT_QUEUE"]

    # --- transactional outbox relay: drains to the real broker ---
    relay = Relay(session_factory=Session_, queue=queue)
    dispatched = relay.drain_once()
    assert dispatched >= 1

    # --- stub text-lane consumer: reads the real envelope off the real queue ---
    delivered = queue.consume(text_queue_name, group="text-lane-e2e", consumer_name="c1", count=1, block_ms=2000)
    assert len(delivered) == 1
    received_envelope = delivered[0].data["work_envelope"]
    assert received_envelope["envelope_id"] == envelope_id
    queue.ack(text_queue_name, "text-lane-e2e", delivered[0].sequence_id)

    with Session_() as session:
        extraction = _stub_text_lane_and_beyond(session, page_id, received_envelope)
        session.commit()
        extraction_id = extraction.id

    # --- decision engine (real): five-outcome routing ---
    with Session_() as session:
        result = route(session, extraction_id)
        session.commit()

    assert result["outcome"] == "auto_accept"

    redis_client.delete(text_queue_name)
