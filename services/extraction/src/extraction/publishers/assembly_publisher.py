"""Publishes Extraction[] / ParcelGeometry[] to ASSEMBLY_QUEUE.
TODO: FR-EXT-04."""
from observability import emit


def publish_text_lane_result(extractions: list[dict], work_envelope: dict, trace_id: str) -> dict:
    return emit(queue="TEXT_QUEUE", producer="text-lane", payload=extractions, work_envelope=work_envelope, trace_id=trace_id)


def publish_map_lane_result(parcel_geometries: list[dict], work_envelope: dict, trace_id: str) -> dict:
    return emit(queue="MAP_QUEUE", producer="map-lane", payload=parcel_geometries, work_envelope=work_envelope, trace_id=trace_id)
