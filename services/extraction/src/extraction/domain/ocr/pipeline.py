"""M3 OCR Vertical Slice Pipeline Execution.

End-to-end execution path:
input page payload + pinned WorkEnvelope
-> OCR engine adapter
-> normalized OCR tokens
-> Extraction-compatible list output.
"""
from __future__ import annotations

import logging
from typing import Any

from .baidu_unlimited import BaiduUnlimitedOCRAdapter
from .field_extractor import extract_fields_for_doctype
from .interfaces import OCREngineAdapter, OCRProcessingError

logger = logging.getLogger(__name__)


def process_page_text(
    page_payload: dict[str, Any],
    work_envelope: dict[str, Any] | None = None,
    ocr_adapter: OCREngineAdapter | None = None,
) -> list[dict[str, Any]]:
    """Executes the end-to-end M3 OCR extraction vertical slice for a single page.

    Guarantees:
    - Accepts standard page payload and pinned WorkEnvelope.
    - Preserves bounding boxes `{x, y, w, h}`.
    - Preserves confidence scores.
    - Attaches `engine`, `model_version`, and `config_version`.
    - Preserves `raw_value` separately from `canonical_value`.
    - Fails safely on empty or unparseable pages returning `[]`.
    - Returns strictly contract-compliant Extraction dict items.
    """
    if not isinstance(page_payload, dict):
        logger.warning("Invalid page_payload: must be a dict")
        return []

    page_id = page_payload.get("page_id") or page_payload.get("id") or "00000000-0000-0000-0000-000000000000"
    doc_type = page_payload.get("doc_type")
    image_bytes = page_payload.get("image_bytes")

    work_env = work_envelope or {}
    config_version = work_env.get("config_version") or "v1"

    # Handle empty/missing image payload safely
    if not image_bytes:
        logger.info("Empty or missing image bytes for page %s; returning empty extractions", page_id)
        return []

    # Default adapter: BaiduUnlimitedOCRAdapter with fallback enabled for dev/test
    adapter = ocr_adapter or BaiduUnlimitedOCRAdapter(allow_test_fallback=True)

    try:
        ocr_result = adapter.process_image(
            image_bytes=image_bytes,
            page_id=page_id,
            config_version=config_version,
        )
    except OCRProcessingError as err:
        logger.warning("OCR processing safely returned no result for page %s: %s", page_id, err)
        return []
    except Exception as err:
        logger.error("Unexpected error during OCR processing for page %s: %s", page_id, err)
        return []

    if not ocr_result.tokens:
        logger.info("OCR returned 0 tokens for page %s", page_id)
        return []

    # Map OCR tokens to Extraction items
    extractions = extract_fields_for_doctype(
        ocr_result=ocr_result,
        page_id=page_id,
        doc_type=doc_type,
    )

    return extractions
