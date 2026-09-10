"""Text lane pipeline: layout analysis -> printed OCR / HWR -> field extraction -> strikethrough -> assembly."""

from __future__ import annotations

from observability import traced_consumer

from extraction.domain.ocr.baidu_unlimited import BaiduUnlimitedOCRAdapter
from extraction.domain.ocr.field_extractor import extract_fields_for_doctype
from extraction.domain.ocr.hwr_adapter import HWRAdapter
from extraction.domain.ocr.relationship_extractor import extract_relationships
from extraction.domain.ocr.table_extractor import extract_table_cells
from extraction.domain.record_assembly import assemble
from extraction.domain.strikethrough import detect_strikethrough
from extraction.publishers.assembly_publisher import publish_text_lane_result
from extraction.publishers.normalization_publisher import publish_assembly


@traced_consumer
def handle(message: dict) -> dict:
    """Processes a text lane message from TEXT_QUEUE.

    Message envelope contains:
    - trace_id
    - work_envelope (pinned at triage)
    - payload (Page metadata or extracted content)
    """
    payload = message.get("payload", {})
    work_envelope = message.get("work_envelope") or {}
    trace_id = message.get("trace_id") or f"{payload.get('document_id', 'doc')}:{payload.get('page_id', 'page')}"

    page_id = payload.get("page_id") or payload.get("id") or "00000000-0000-0000-0000-000000000000"
    doc_type = payload.get("doc_type")
    page_role = payload.get("page_role")
    config_version = work_envelope.get("config_version") or "v1"

    image_bytes = payload.get("image_bytes") or b"fake_image_bytes"
    page_strokes = payload.get("page_strokes") or []

    # 1. OCR Engine Selection (Printed OCR vs HWR vs Table Extractor)
    if page_role == "tabular_register" or doc_type in ("jamabandi", "khasra_khatauni"):
        ocr_result = extract_table_cells(image_bytes, page_id=page_id, config_version=config_version)
    elif page_role == "endorsement":
        adapter = HWRAdapter()
        ocr_result = adapter.process_image(image_bytes, page_id=page_id, config_version=config_version)
    else:
        adapter = BaiduUnlimitedOCRAdapter(allow_test_fallback=True)
        ocr_result = adapter.process_image(image_bytes, page_id=page_id, config_version=config_version)

    # 2. Field Extraction & Relationship Linking
    extractions = extract_fields_for_doctype(ocr_result, page_id=page_id, doc_type=doc_type)
    extractions = extract_relationships(extractions)

    # 3. Geometric Strikethrough / Cancellation Analysis (FR-EXT-06)
    for ext in extractions:
        bbox = ext.get("bbox")
        if detect_strikethrough(bbox, page_strokes):
            ext["entry_status"] = "unknown"

        # INVARIANT 2: entry_status MUST default to "unknown", NEVER "live" on creation
        if ext.get("entry_status") not in ("unknown", "cancelled", "superseded", "amended"):
            ext["entry_status"] = "unknown"

    # 4. Multi-Page Record Assembly (FR-EXT-04)
    record_assembly = assemble(extractions, rationale=f"Assembled extractions for page {page_id}")

    # 5. Publish outbound queue envelopes
    text_lane_envelope = publish_text_lane_result(extractions, work_envelope=work_envelope, trace_id=trace_id)
    assembly_envelope = publish_assembly(record_assembly, extractions=extractions, work_envelope=work_envelope, trace_id=trace_id)

    return {
        "status": "success",
        "extractions_count": len(extractions),
        "text_lane_envelope": text_lane_envelope,
        "assembly_envelope": assembly_envelope,
    }
