"""Map lane pipeline: georeference -> vectorize -> bind survey labels ->
compute area -> ULPIN eligibility gate -> MAP_QUEUE envelope.
"""
from __future__ import annotations

from observability import traced_consumer

from extraction.domain.parcel_pipeline import process_map_page
from extraction.publishers.assembly_publisher import publish_map_lane_result


@traced_consumer
def handle(message: dict) -> dict:
    """Processes a map lane message from MAP_QUEUE."""
    payload = message.get("payload", {}) if isinstance(message, dict) else {}
    work_envelope = message.get("work_envelope") or {} if isinstance(message, dict) else {}
    trace_id = message.get("trace_id") or f"{payload.get('document_id', 'map_doc')}:{payload.get('page_id', 'map_page')}"

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
