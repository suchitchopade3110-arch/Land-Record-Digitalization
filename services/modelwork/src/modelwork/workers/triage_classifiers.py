"""Consumes TRIAGE_QUEUE (producer="ingest") and runs legibility scoring,
script/language/doc-type/page-role classification, anonymous writer clustering,
and novelty pre-score (FR-TRI-01..04/10/11, FR-CNF-14).

Emits classified Page back to TRIAGE_QUEUE with producer="triage" for downstream
consumption by Backend's triage_router.
DO NOT publish directly to TEXT_QUEUE or MAP_QUEUE (Backend owns downstream routing).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from modelwork.domain.calibration.novelty import (
    NoveltyDetector,
    NoveltyPolicy,
)
from modelwork.domain.triage.legibility import (
    DeterministicLegibilityScorer,
    LegibilityPolicy,
    LegibilityResult,
    LegibilityScorer,
)
from modelwork.domain.triage.novelty_prescore import compute_triage_novelty_prescore
from modelwork.domain.triage.script_doctype import (
    DeterministicScriptDoctypeClassifier,
    ScriptDoctypeResult,
    TriageClassifier,
)
from modelwork.domain.triage.writer_clustering import (
    DeterministicWriterClusterer,
    WriterClusterer,
    WriterClusterResult,
)
from modelwork.publishers.triage_publisher import publish as publish_triage

try:
    from observability import traced_consumer
except ImportError:  # pragma: no cover
    def traced_consumer(fn: Callable) -> Callable:
        return fn

logger = logging.getLogger(__name__)

# Canonical producer enums per contracts/asyncapi/triage-queue.yaml
VALID_PRODUCERS: frozenset[str] = frozenset({"ingest", "triage"})


def _lane_for(*, doc_type: str | None, page_role: str | None) -> list[str]:
    """Pure routing helper for FR-TRI-05 (text/map fork)."""
    if page_role == "blank":
        return []
    lanes: list[str] = []
    if page_role in ("text", "tabular_register", "endorsement"):
        lanes.append("text")
    if page_role == "map_sheet" or doc_type in ("cadastral_map", "fmb_sketch"):
        lanes.append("map")
    return lanes


@traced_consumer
def handle(
    message: dict[str, Any],
    session: Any = None,
    *,
    legibility_scorer: LegibilityScorer | None = None,
    legibility_policy: LegibilityPolicy | None = None,
    script_classifier: TriageClassifier | None = None,
    writer_clusterer: WriterClusterer | None = None,
    novelty_detector: NoveltyDetector | None = None,
    novelty_policy: NoveltyPolicy | None = None,
    image_loader: Callable[[str, str], bytes] | None = None,
    rescan_handler: Callable[[str, str], Any] | None = None,
    publisher: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Execute triage classification and legibility scoring for an ingested page.

    Steps:
    1. Validate queue contract producer and required envelope fields.
    2. Hydrate image bytes via loader / storage / message payload.
    3. Run legibility scorer, script/doc_type classifier, anonymous writer clusterer,
       and novelty pre-score.
    4. Handle rescan task creation idempotently if legibility threshold breached.
    5. Construct contract-valid Page payload adhering to contracts/schemas/page.schema.json.
    6. Publish back to TRIAGE_QUEUE with producer="triage".
    """
    producer = message.get("producer")
    if producer not in VALID_PRODUCERS:
        raise ValueError(
            f"Unsupported producer '{producer}' on TRIAGE_QUEUE. Expected one of {sorted(VALID_PRODUCERS)}"
        )

    # Echo prevention: if producer is already 'triage', this is outbound output meant for triage_router
    if producer == "triage":
        logger.info("Skipping message already triaged (producer='triage')")
        return {"status": "skipped", "reason": "already_triaged"}

    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Message missing dictionary 'payload'")  # noqa: TRY004

    page_id = payload.get("id")
    document_id = payload.get("document_id")
    page_index = payload.get("index")

    if not page_id or not document_id or page_index is None:
        raise ValueError("Payload missing required Page fields: 'id', 'document_id', or 'index'")

    trace_id = message.get("trace_id", f"{document_id}:{page_id}")

    # 1. Hydrate image bytes
    page_image = b""
    if image_loader is not None:
        page_image = image_loader(document_id, page_id)
    elif "_image_bytes" in message:
        page_image = message["_image_bytes"]
    elif session is not None:
        try:
            from backend.models.entities import Page
            from landstorage import get_store

            page_entity = session.get(Page, page_id)
            if page_entity is not None and getattr(page_entity, "storage_uri", None):
                store = get_store()
                page_image = store.get(page_entity.storage_uri)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not hydrate image from session/storage: %s", exc)

    # 2. Run Legibility Scorer (FR-TRI-01, FR-TRI-10)
    active_legibility_scorer = legibility_scorer or DeterministicLegibilityScorer()
    active_legibility_policy = legibility_policy or LegibilityPolicy()
    leg_res: LegibilityResult = active_legibility_scorer.score(
        page_image, policy=active_legibility_policy
    )

    # 3. Run Script, Language, Doc-Type, Page-Role Classifiers (FR-TRI-02..04)
    active_script_classifier = script_classifier or DeterministicScriptDoctypeClassifier()
    sd_res: ScriptDoctypeResult = active_script_classifier.classify(page_image)

    # 4. Run Anonymous Writer Clustering (FR-TRI-11, PRD §11 Q10)
    active_writer_clusterer = writer_clusterer or DeterministicWriterClusterer()
    wc_res: WriterClusterResult = active_writer_clusterer.cluster(page_image)

    # 5. Run Triage Novelty Pre-Score (FR-CNF-14)
    novelty_score = compute_triage_novelty_prescore(
        doc_type=sd_res.doc_type,
        script=sd_res.script,
        legibility_band=leg_res.legibility_band,
        writer_cluster_id=wc_res.writer_cluster_id,
        detector=novelty_detector,
        policy=novelty_policy,
    )

    # 6. Idempotent RescanTask trigger on threshold breach (FR-TRI-01)
    if leg_res.is_breached:
        reason = leg_res.reason_code or "quality_below_threshold"
        if rescan_handler is not None:
            rescan_handler(page_id, reason)
        elif session is not None:
            try:
                from backend.domain.rescan import open_from_threshold_breach
                from backend.models.entities import RescanTask

                existing_task = (
                    session.query(RescanTask)
                    .filter(RescanTask.page_id == page_id, RescanTask.state != "closed")
                    .first()
                )
                if existing_task is None:
                    open_from_threshold_breach(session, page_id=page_id, reason_code=reason)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to record RescanTask via session: %s", exc)

    # 7. Compute optional route list per FR-TRI-05
    routes = _lane_for(doc_type=sd_res.doc_type, page_role=sd_res.page_role)

    # 8. Build contract-adherent Page payload (page.schema.json, additionalProperties: false)
    classified_page: dict[str, Any] = {
        "id": page_id,
        "document_id": document_id,
        "index": page_index,
        "index_position": payload.get("index_position"),
        "quality_score": leg_res.quality_score,
        "legibility_band": leg_res.legibility_band,
        "script": sd_res.script,
        "language": sd_res.language,
        "doc_type": sd_res.doc_type,
        "page_role": sd_res.page_role,
        "writer_cluster_id": wc_res.writer_cluster_id,
        "novelty_score": novelty_score,
        "route": routes,
    }

    # 9. Update DB entity if session provided
    if session is not None:
        try:
            from backend.models.entities import Page

            db_page = session.get(Page, page_id)
            if db_page is not None:
                db_page.quality_score = leg_res.quality_score
                db_page.legibility_band = leg_res.legibility_band
                db_page.script = sd_res.script
                db_page.language = sd_res.language
                db_page.doc_type = sd_res.doc_type
                db_page.page_role = sd_res.page_role
                db_page.writer_cluster_id = wc_res.writer_cluster_id
                db_page.novelty_score = novelty_score
                db_page.route = routes
                session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not flush classified Page to DB: %s", exc)

    # 10. Publish outbound envelope to TRIAGE_QUEUE with producer="triage"
    outbound = publish_triage(
        page=classified_page,
        trace_id=trace_id,
        work_envelope=message.get("work_envelope"),
    )

    # 11. Record in outbox if session provided
    if session is not None:
        try:
            from landoutbox import write as outbox_write

            outbox_write(session, queue="TRIAGE_QUEUE", envelope=outbound)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Could not write outbox message: %s", exc)

    if publisher is not None:
        publisher(outbound)

    return outbound
