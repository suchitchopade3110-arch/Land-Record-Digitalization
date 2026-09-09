"""M2 triage *routing* (not the classifiers — those are Tharun's, per
`Team-Split-4-Persons.md`: "Backend owns the queue and routing
infrastructure around triage, not the classifiers that decide where a page
goes"). FR-TRI-05/09: pin the work envelope once, route to TEXT_QUEUE
and/or MAP_QUEUE.
"""
from __future__ import annotations

from collections.abc import Callable

from landenvelope import pin
from landenvelope.pin import WorkEnvelope
from landoutbox import write as outbox_write
from observability.envelope import emit
from sqlalchemy.orm import Session

ResolveModelVersions = Callable[[], dict[str, str]]


def _stub_resolve_model_versions() -> dict[str, str]:
    """TODO: FR-TRI-09 — call Tharun's `GET /models/{module}/active` once
    per page (API-Contracts §4.2) to resolve real model versions. Tharun's
    model registry does not exist yet (Team-Split: Person 4's P0 scope),
    so this stand-in returns a fixed, clearly-labeled placeholder version
    for all five models the envelope requires — schema-valid fake output,
    replaceable with zero caller changes once the real registry ships, per
    Rule 0's "stub worker" contract."""
    return {
        "triage_classifier": "stub-v0",
        "printed_ocr": "stub-v0",
        "hwr": "stub-v0",
        "confidence_calibrator": "stub-v0",
        "novelty_detector": "stub-v0",
    }


def route_page(
    session: Session,
    *,
    document_id: str,
    page_id: str,
    doc_type: str,
    page_role: str,
    config_version: str,
    resolve_model_versions: ResolveModelVersions = _stub_resolve_model_versions,
) -> tuple[WorkEnvelope, list[str]]:
    """Pin the work envelope (once — FR-TRI-09) and enqueue the page onto
    TEXT_QUEUE and/or MAP_QUEUE per `lane_for`. Both the pin and the
    outbox writes happen in `session`'s transaction, uncommitted — the
    caller (the queue worker) commits once, so "pinned" and "queued the
    next hop(s)" land together (ADR-005).
    """
    envelope = pin(
        session,
        document_id=document_id,
        page_id=page_id,
        model_versions=resolve_model_versions(),
        config_version=config_version,
    )

    lanes = lane_for(doc_type=doc_type, page_role=page_role)
    trace_id = f"{document_id}:{page_id}"
    queued: list[str] = []
    for queue_name in lanes:
        envelope_msg = emit(
            queue=queue_name,
            producer="triage",
            payload={"document_id": document_id, "page_id": page_id, "doc_type": doc_type, "page_role": page_role},
            work_envelope=envelope.to_contract_dict(),
            trace_id=trace_id,
        )
        outbox_write(session, queue=queue_name, envelope=envelope_msg)
        queued.append(queue_name)
    return envelope, queued


def lane_for(*, doc_type: str, page_role: str) -> list[str]:
    """FR-TRI-05 — route to the text lane, the map lane, or both. A page
    carrying a register table and a sketch enters both, so the two checks
    below are independent `if`s, not a chain of `elif`s. A `blank` page
    (FR-TRI-04) enters neither — there is nothing to extract.
    """
    if page_role == "blank":
        return []

    lanes = []
    if page_role in ("text", "tabular_register", "endorsement"):
        lanes.append("TEXT_QUEUE")
    if page_role == "map_sheet" or doc_type in ("cadastral_map", "fmb_sketch"):
        lanes.append("MAP_QUEUE")
    return lanes
