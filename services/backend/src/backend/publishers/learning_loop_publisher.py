"""Publishes Correction rows to LEARNING_LOOP_QUEUE on officer submit or confirmation.
FR-LRN-01/07/11/12.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from landenvelope import find_by_page
from landoutbox import write as outbox_write
from observability import emit
from sqlalchemy.orm import Session

from backend.models.entities import Correction, Extraction, Page

QUEUE_NAME = "LEARNING_LOOP_QUEUE"


def build_learning_loop_envelope(
    correction_payload: dict[str, Any],
    work_envelope: dict[str, Any] | None,
    trace_id: str,
    producer: str = "review",
) -> dict[str, Any]:
    """Build a standard LearningLoopEnvelope conforming to contracts/asyncapi/learning-loop-queue.yaml."""
    return emit(
        queue=QUEUE_NAME,
        producer=producer,
        payload=correction_payload,
        work_envelope=work_envelope,
        trace_id=trace_id,
    )


def publish_correction_to_outbox(
    session: Session,
    correction: Correction,
    extraction: Extraction | None = None,
    producer: str = "review",
) -> dict[str, Any]:
    """Stage a Correction for transactional outbox dispatch to LEARNING_LOOP_QUEUE (FR-LRN-01/07).

    Preserves page-level provenance and transactional atomicity via ADR-005 outbox.
    """
    if extraction is None:
        extraction = session.get(Extraction, correction.extraction_id)

    page_id = extraction.page_id if extraction is not None else None
    envelope = find_by_page(session, page_id) if page_id else None

    if envelope is not None:
        work_env_dict = envelope.to_contract_dict()
        doc_id = envelope.document_id
    else:
        page = session.get(Page, page_id) if page_id else None
        doc_id = page.document_id if page else str(uuid.uuid4())
        model_ver = (extraction.model_version if extraction else None) or correction.model_version or "unversioned"
        cfg_ver = (extraction.config_version if extraction else None) or correction.config_version or "v1"
        work_env_dict = {
            "envelope_id": str(uuid.uuid4()),
            "document_id": doc_id,
            "page_id": page_id or str(uuid.uuid4()),
            "pinned_at": datetime.now(timezone.utc).isoformat(),
            "model_versions": {
                "triage_classifier": model_ver,
                "printed_ocr": model_ver,
                "hwr": model_ver,
                "confidence_calibrator": model_ver,
                "novelty_detector": model_ver,
            },
            "config_version": cfg_ver,
        }

    trace_id = f"{doc_id}:{page_id}" if (doc_id and page_id) else correction.id
    envelope_msg = build_learning_loop_envelope(
        correction_payload=correction.to_contract_dict(),
        work_envelope=work_env_dict,
        trace_id=trace_id,
        producer=producer,
    )
    outbox_write(session, queue=QUEUE_NAME, envelope=envelope_msg)
    return envelope_msg
