"""Consumes TRIAGE_QUEUE after Tharun's classifiers have run. Pins the work
envelope (FR-TRI-09) and routes to TEXT_QUEUE / MAP_QUEUE / both.

This is the idempotence guarantee everything downstream depends on: a
retried message after a model promotion must reproduce its original
result, never a new one (API-Contracts-and-Interfaces.md §2, §7 rule 1).
`backend.domain.triage.route_page` is the actual logic; this module is the
queue-consumer shell around it.
"""
from __future__ import annotations

from observability import traced_consumer
from sqlalchemy.orm import Session

from backend.domain.triage import route_page


@traced_consumer
def handle(message: dict, session: Session) -> dict:
    """`message["payload"]` is a `Page`-shaped dict per
    `contracts/schemas/page.schema.json`, fully classified by Tharun's
    triage classifiers (FR-TRI-01–04) before this worker ever sees it —
    this worker only decides the *routing*, per Team-Split's ownership
    split. `config_version` for this pass is read from the message if the
    upstream ingest stage already resolved one; a real config-service
    lookup (FR-CFG-01/02) replaces the `"unversioned"` fallback once
    `services/backend`'s config service (M13) is wired to actually serve
    versions rather than raising `NotImplementedError`
    (`backend.config._fetch_from_db`).
    """
    payload = message["payload"]
    envelope, queued_to = route_page(
        session,
        document_id=payload["document_id"],
        page_id=payload["id"],
        doc_type=payload["doc_type"],
        page_role=payload["page_role"],
        config_version=payload.get("config_version", "unversioned"),
    )
    session.commit()
    return {"envelope_id": envelope.envelope_id, "queued_to": queued_to}
