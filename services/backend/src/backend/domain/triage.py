"""M2 triage *routing* (not the classifiers — those are Tharun's, per
`Team-Split-4-Persons.md`: "Backend owns the queue and routing
infrastructure around triage, not the classifiers that decide where a page
goes"). FR-TRI-05/09: pin the work envelope once, route to TEXT_QUEUE
and/or MAP_QUEUE.
"""
from __future__ import annotations

from collections.abc import Callable

from landenvelope import find_by_page, pin
from landenvelope.pin import WorkEnvelope
from landoutbox import write as outbox_write
from observability.envelope import emit
from sqlalchemy.orm import Session

from backend.domain.model_registry_client import resolve_active_model_versions

ResolveModelVersions = Callable[[], dict[str, str]]


def route_page(
    session: Session,
    *,
    document_id: str,
    page_id: str,
    doc_type: str,
    page_role: str,
    config_version: str,
    resolve_model_versions: ResolveModelVersions = resolve_active_model_versions,
) -> tuple[WorkEnvelope, list[str]]:
    """Pin the work envelope (once — FR-TRI-09) and enqueue the page onto
    TEXT_QUEUE and/or MAP_QUEUE per `lane_for`. Both the pin and the
    outbox writes happen in `session`'s transaction, uncommitted — the
    caller (the queue worker) commits once, so "pinned" and "queued the
    next hop(s)" land together (ADR-005).

    Replay-safe (T2.d, FR-TRI-09): if `page_id` already has a pinned
    envelope (a redelivered/replayed triage message), `resolve_model_versions`
    is never even called — "never resolve current model mid-pipeline"
    means not asking the registry a second time, not just discarding its
    answer — and the existing envelope is reused unchanged. `pin()` itself
    would also catch this (get-or-create by page_id), but checking here
    first is what avoids the pointless registry round-trip on a replay.
    """
    envelope = find_by_page(session, page_id)
    if envelope is None:
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

    # P5-06-fix/FR-ANL-01 — "processed" no longer fires here. `route_page`
    # is routing (envelope pinned, enqueued for extraction), not a
    # decision about the page's content — see `backend.domain.audit_log
    # .record_page_processed`'s docstring for the settled definition and
    # `backend.domain.page_lifecycle` for where `page.processed` actually
    # fires now (the decision engine, once every field is terminal).

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
