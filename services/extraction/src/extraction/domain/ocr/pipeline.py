"""M3 OCR Vertical Slice Pipeline Execution.

End-to-end execution path:
input page payload + pinned WorkEnvelope
-> OCR engine adapter
-> normalized OCR tokens
-> Extraction-compatible list output + Entity Relationship Bindings.
"""
from __future__ import annotations

import logging
from typing import Any

from .baidu_unlimited import BaiduUnlimitedOCRAdapter
from .field_extractor import extract_fields_for_doctype
from .hwr_adapter import HWRAdapter
from .interfaces import OCREngineAdapter, OCRProcessingError
from .region_router import LogicalRoute, RegionDispatcher, RegionRouter
from .relationship_extractor import extract_relationships

logger = logging.getLogger(__name__)


def process_page_text(
    page_payload: dict[str, Any],
    work_envelope: dict[str, Any] | None = None,
    ocr_adapter: OCREngineAdapter | None = None,
) -> list[dict[str, Any]]:
    """Executes the end-to-end M3 OCR extraction vertical slice for a single page.

    Returns strictly contract-compliant Extraction dict items.
    """
    extractions, _ = process_page_text_with_relationships(
        page_payload=page_payload,
        work_envelope=work_envelope,
        ocr_adapter=ocr_adapter,
    )
    return extractions


def process_page_text_with_relationships(
    page_payload: dict[str, Any],
    work_envelope: dict[str, Any] | None = None,
    ocr_adapter: OCREngineAdapter | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Executes OCR extraction and relationship binding, returning `(extractions, relationships)`.

    Extractions remain 100% compliant with `contracts/schemas/extraction.schema.json`.
    Relationships detail owner ↔ share ↔ parcel entity bindings.
    """
    if not isinstance(page_payload, dict):
        logger.warning("Invalid page_payload: must be a dict")
        return [], []

    page_id = page_payload.get("page_id") or page_payload.get("id") or "00000000-0000-0000-0000-000000000000"
    doc_type = page_payload.get("doc_type")
    image_bytes = page_payload.get("image_bytes")

    work_env = work_envelope or {}
    config_version = work_env.get("config_version") or "v1"

    # Handle empty/missing image payload safely
    if not image_bytes:
        logger.info("Empty or missing image bytes for page %s; returning empty extractions", page_id)
        return [], []

    # Default adapter: BaiduUnlimitedOCRAdapter with fallback enabled for dev/test
    adapter = ocr_adapter or BaiduUnlimitedOCRAdapter(allow_test_fallback=True)

    # 1. Region Routing (FR-OCR-04 / FR-TRI-05)
    router = RegionRouter()
    routing_plan = router.route_page(page_payload)

    engine_map = {
        LogicalRoute.PRINTED_TEXT: adapter,
        LogicalRoute.HANDWRITTEN_TEXT: HWRAdapter(),
    }
    dispatcher = RegionDispatcher(engine_map=engine_map)

    try:
        ocr_results = dispatcher.dispatch_and_extract(
            page_payload=page_payload,
            routing_plan=routing_plan,
            config_version=config_version,
        )
    except OCRProcessingError as err:
        logger.warning("OCR processing safely returned no result for page %s: %s", page_id, err)
        return [], []
    except Exception:
        logger.exception("Unexpected error during OCR processing for page %s", page_id)
        return [], []

    # 2. Extract fields across routed OCR results
    extractions: list[dict[str, Any]] = []
    for ocr_res in ocr_results:
        if not ocr_res.tokens:
            continue
        exts = extract_fields_for_doctype(
            ocr_result=ocr_res,
            page_id=page_id,
            doc_type=doc_type,
        )
        extractions.extend(exts)

    if not extractions:
        logger.info("OCR returned 0 tokens/extractions for page %s", page_id)
        return [], []

    # 3. Extract structural entity relationships (owner ↔ share ↔ parcel)
    relationships = extract_relationships(extractions, page_id=page_id)

    return extractions, relationships
