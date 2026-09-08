"""Publishes RecordAssembly + raw Extraction[] to NORMALIZATION_QUEUE.
TODO: FR-NRM-01-07."""
from observability import emit


def publish_assembly(record_assembly: dict, extractions: list[dict], work_envelope: dict, trace_id: str) -> dict:
    return emit(
        queue="ASSEMBLY_QUEUE",
        producer="assembly",
        payload={"record_assembly": record_assembly, "extractions": extractions},
        work_envelope=work_envelope,
        trace_id=trace_id,
    )
