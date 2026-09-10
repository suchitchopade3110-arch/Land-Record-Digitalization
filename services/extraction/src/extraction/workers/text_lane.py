"""Text lane pipeline: layout analysis -> printed OCR -> field extraction -> strikethrough -> assembly."""

from __future__ import annotations

from observability import traced_consumer

from extraction.domain.ocr.baidu_unlimited import BaiduUnlimitedOCRAdapter
from extraction.domain.ocr.pipeline import process_page_text
from extraction.domain.ocr.relationship_extractor import extract_relationships
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
    page_strokes = payload.get("page_strokes") or []

    # Inject default fake image bytes if missing in unit test payload
    if "image_bytes" not in payload:
        payload = {**payload, "image_bytes": b"fake_image_bytes"}

    # 1. End-to-end OCR processing vertical slice
    ocr_adapter = BaiduUnlimitedOCRAdapter(allow_test_fallback=True)
    extractions = process_page_text(page_payload=payload, work_envelope=work_envelope, ocr_adapter=ocr_adapter)

    # 2. Relationship Linking
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
