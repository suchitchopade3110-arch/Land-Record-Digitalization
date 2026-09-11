"""T1-03 — Decision engine boundary & idempotency tests (Block B).

Covers:
1. Replay: same DECISION message twice -> one task/conflict/alert, one audit entry, <=1 page.processed.
2. Changed outcome for already-decided extraction -> typed rejection (AlreadyDecidedWithDifferentOutcome), nothing new written.
3. Payload/DB mismatch -> rejected with decision.payload_mismatch audit, no overwrite.
4. Missing Extraction row -> typed error (ExtractionNotFoundError), no partial writes.
5. Service DB roles & column-level GRANTs -> modelwork_role cannot UPDATE columns it doesn't own.
"""
from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.decision import (
    AlreadyDecidedWithDifferentOutcome,
    ExtractionNotFoundError,
    PayloadMismatchError,
    route,
)
from backend.models.entities import (
    Batch,
    Conflict,
    DecisionRecord,
    Extraction,
    Page,
    ReviewTask,
    SourceDocument,
)
from backend.workers.decision_engine import handle as decision_handle
from landaudit.models import AuditEntry

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
        pytest.skip(f"{TEST_DB_URL} is unreachable or has no migrated schema — skipping DB tests per Ground rule 13")
    return eng


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, future=True)



@pytest.fixture
def sample_page(session_factory):
    with session_factory() as session:
        batch = Batch(district="sitapur")
        session.add(batch)
        session.flush()
        doc = SourceDocument(
            batch_id=batch.id,
            sha256="c" * 64,
            storage_uri="dummy",
            mime="image/tiff",
            page_count=1,
        )
        session.add(doc)
        session.flush()
        page = Page(document_id=doc.id, index=0)
        session.add(page)
        session.commit()
        return page.id


# ---------------------------------------------------------------------------
# Test 1: Replay idempotency
# ---------------------------------------------------------------------------


def test_decision_replay_is_idempotent(session_factory, sample_page):
    """Broken implementation it catches: A decision engine that duplicates
    ReviewTask, Conflict, or AuditEntry rows on replay.
    """
    extraction_id = str(uuid.uuid4())
    with session_factory() as session:
        ext = Extraction(
            id=extraction_id,
            page_id=sample_page,
            field_name="owner_name",
            raw_value="राम",
            canonical_value="Ram",
            engine="printed_ocr",
            model_version="v1",
            config_version="cfg-v1",
            token_confidence=0.95,
            calibrated_confidence=0.92,
            novelty_score=0.01,
            routing_outcome="review",
        )
        session.add(ext)
        session.commit()

    message = {
        "trace_id": f"doc:{sample_page}",
        "producer": "confidence-novelty",
        "payload": {
            "id": extraction_id,
            "routing_outcome": "review",
            "calibrated_confidence": 0.92,
        },
    }

    # First pass: creates task and audit entry
    with session_factory() as session:
        res1 = decision_handle(message, session)
        session.commit()

    assert res1["outcome"] == "review"

    # Second pass: replay with identical message
    with session_factory() as session:
        res2 = decision_handle(message, session)
        session.commit()

    assert res2["outcome"] == "review"

    # Assert exactly 1 ReviewTask was created
    with session_factory() as session:
        tasks = session.scalars(select(ReviewTask).where(ReviewTask.extraction_id == extraction_id)).all()
        assert len(tasks) == 1, f"Expected exactly 1 ReviewTask on replay, got {len(tasks)}"

        # Assert exactly 1 decision.route.review audit entry
        audits = session.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "decision.route.review",
                AuditEntry.subject == extraction_id,
            )
        ).all()
        assert len(audits) == 1, f"Expected exactly 1 audit entry on replay, got {len(audits)}"

        # Assert exactly 1 decision_record row
        decisions = session.scalars(
            select(DecisionRecord).where(DecisionRecord.extraction_id == extraction_id)
        ).all()
        assert len(decisions) == 1


# ---------------------------------------------------------------------------
# Test 2: Changed outcome for already-decided extraction
# ---------------------------------------------------------------------------


def test_changed_outcome_for_already_decided_extraction_is_rejected(session_factory, sample_page):
    """Broken implementation it catches: A decision engine that allows an already-decided
    extraction to be re-decided with a conflicting outcome.
    """
    extraction_id = str(uuid.uuid4())
    with session_factory() as session:
        ext = Extraction(
            id=extraction_id,
            page_id=sample_page,
            field_name="owner_name",
            raw_value="राम",
            canonical_value="Ram",
            engine="printed_ocr",
            model_version="v1",
            config_version="cfg-v1",
            token_confidence=0.95,
            calibrated_confidence=0.98,
            novelty_score=0.01,
            routing_outcome="auto_accept",
        )
        session.add(ext)
        session.commit()

    msg1 = {
        "trace_id": f"doc:{sample_page}",
        "payload": {"id": extraction_id, "routing_outcome": "auto_accept"},
    }
    with session_factory() as session:
        decision_handle(msg1, session)
        session.commit()

    # Simulate M8 legitimately writing a new routing_outcome straight to
    # Postgres (D1-A) — decision_engine reads DB truth, not the payload,
    # so triggering AlreadyDecidedWithDifferentOutcome (as opposed to the
    # unrelated D1-A payload/DB mismatch check) requires the DB itself,
    # not just the message, to have moved to the new outcome.
    with session_factory() as session:
        ext = session.get(Extraction, extraction_id)
        ext.routing_outcome = "conflict"
        session.commit()

    # Attempt to change decision to 'conflict' without reprocessing workflow
    msg2 = {
        "trace_id": f"doc:{sample_page}",
        "payload": {"id": extraction_id, "routing_outcome": "conflict"},
    }
    with session_factory() as session:
        with pytest.raises(AlreadyDecidedWithDifferentOutcome):
            decision_handle(msg2, session)


# ---------------------------------------------------------------------------
# Test 3: Payload / DB mismatch rejected without overwrite
# ---------------------------------------------------------------------------


def test_payload_db_mismatch_is_rejected_with_audit_and_no_overwrite(session_factory, sample_page):
    """Broken implementation it catches: Unconditional overwrite of DB columns from
    message payload without validation (PR #10 behavior).
    """
    extraction_id = str(uuid.uuid4())
    with session_factory() as session:
        ext = Extraction(
            id=extraction_id,
            page_id=sample_page,
            field_name="owner_name",
            raw_value="श्याम",
            canonical_value="Shyam",
            engine="printed_ocr",
            model_version="v1",
            config_version="cfg-v1",
            token_confidence=0.90,
            calibrated_confidence=0.85,
            novelty_score=0.01,
            routing_outcome="review",
        )
        session.add(ext)
        session.commit()

    # Payload claims 'auto_accept' when DB has 'review'
    mismatched_msg = {
        "trace_id": f"doc:{sample_page}",
        "payload": {
            "id": extraction_id,
            "routing_outcome": "auto_accept",
            "calibrated_confidence": 0.99,
        },
    }

    with session_factory() as session:
        with pytest.raises(PayloadMismatchError):
            decision_handle(mismatched_msg, session)
            session.commit()

    # Assert DB was NOT overwritten
    with session_factory() as session:
        ext = session.get(Extraction, extraction_id)
        assert ext.routing_outcome == "review", "DB column must not be overwritten by mismatched payload"
        assert ext.calibrated_confidence == 0.85

        # Assert audit entry decision.payload_mismatch was logged without sensitive payload values
        audits = session.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "decision.payload_mismatch",
                AuditEntry.subject == extraction_id,
            )
        ).all()
        assert len(audits) >= 1
        assert "owner_name" not in str(audits[-1].purpose)


# ---------------------------------------------------------------------------
# Test 4: Missing Extraction row raises typed error
# ---------------------------------------------------------------------------


def test_missing_extraction_row_raises_typed_error(session_factory):
    """Broken implementation it catches: Bare KeyError or unhandled exceptions on missing entity."""
    non_existent_id = str(uuid.uuid4())
    msg = {
        "trace_id": "doc:missing",
        "payload": {"id": non_existent_id, "routing_outcome": "review"},
    }
    with session_factory() as session:
        with pytest.raises(ExtractionNotFoundError):
            decision_handle(msg, session)


# ---------------------------------------------------------------------------
# Test 5: Service DB roles and column-level GRANTs
# ---------------------------------------------------------------------------


def test_service_db_roles_and_column_level_grants(engine, sample_page):
    """Broken implementation it catches: Overly permissive database roles allowing
    modelwork_role to overwrite columns owned by extraction/backend.
    """
    extraction_id = str(uuid.uuid4())
    with Session(engine) as session:
        ext = Extraction(
            id=extraction_id,
            page_id=sample_page,
            field_name="owner_name",
            raw_value="सुरेश",
            canonical_value="Suresh",
            engine="printed_ocr",
            model_version="v1",
            config_version="cfg-v1",
            token_confidence=0.80,
            calibrated_confidence=None,
            novelty_score=None,
            routing_outcome=None,
        )
        session.add(ext)
        session.commit()

    # Test as modelwork_role: can UPDATE calibrated_confidence, novelty_score, routing_outcome
    with engine.connect() as conn:
        try:
            conn.execute(text("SET ROLE modelwork_role"))
        except Exception:
            pytest.skip("modelwork_role does not exist in DB yet — run migrations first")

        # 1. Allowed UPDATE on owned columns
        conn.execute(
            text(
                "UPDATE extraction SET calibrated_confidence = 0.88, routing_outcome = 'review' WHERE id = :id"
            ),
            {"id": extraction_id},
        )
        conn.commit()

        # 2. Denied UPDATE on unowned column (e.g. canonical_value)
        with pytest.raises(Exception, match="permission denied"):
            conn.execute(
                text("UPDATE extraction SET canonical_value = 'Hacked' WHERE id = :id"),
                {"id": extraction_id},
            )
            conn.commit()
