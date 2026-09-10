"""Map lane pipeline: georeference -> vectorize -> bind survey labels ->
compute area -> ULPIN eligibility gate -> MAP_QUEUE envelope.
"""
from __future__ import annotations

import logging

from observability import traced_consumer

from extraction.domain.parcel_pipeline import process_map_page
from extraction.publishers.assembly_publisher import publish_map_lane_result

logger = logging.getLogger(__name__)


@traced_consumer
def handle(message: dict) -> dict:
    """Processes a map lane message from MAP_QUEUE."""
    if not isinstance(message, dict):
        message = {}

    payload = message.get("payload", {}) if isinstance(message.get("payload"), dict) else {}
    work_envelope = message.get("work_envelope") or {} if isinstance(message.get("work_envelope"), dict) else {}
    trace_id = message.get("trace_id") or f"{payload.get('document_id', 'map_doc')}:{payload.get('page_id', 'map_page')}"

    try:
        parcel_geometries = process_map_page(map_payload=payload, work_envelope=work_envelope)

        map_lane_envelope = publish_map_lane_result(
            parcel_geometries=parcel_geometries,
            work_envelope=work_envelope,
            trace_id=trace_id,
        )

        return {
            "status": "success",
            "parcel_geometries_count": len(parcel_geometries),
            "map_lane_envelope": map_lane_envelope,
        }
    except Exception as err:
        logger.exception("Map lane processing failed for trace_id=%s", trace_id)
        return {
            "status": "error",
            "error_type": type(err).__name__,
            "error_message": str(err),
            "parcel_geometries_count": 0,
        }
