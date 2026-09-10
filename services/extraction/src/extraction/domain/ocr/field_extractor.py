"""Field Extraction against Document Type Schemas (FR-EXT-01).

Maps raw OCR tokens into contract-compliant `Extraction` dictionary items.
Guarantees `entry_status` defaults to 'unknown' (Invariant 2).
"""
from __future__ import annotations

import uuid

from .interfaces import OCRResult
from .normalization import get_unconstrained_value, normalize_text


def extract_fields_for_doctype(
    ocr_result: OCRResult,
    page_id: str | None = None,
    doc_type: str | None = None,
) -> list[dict]:
    """Converts an OCRResult into a list of Extraction dicts matching `extraction.schema.json`."""
    extractions: list[dict] = []
    target_page_id = page_id or ocr_result.page_id

    for token in ocr_result.tokens:
        raw_val = token.text
        if not raw_val:
            continue

        field_name = _infer_field_name(raw_val, doc_type)
        canonical_val = normalize_text(raw_val)
        unconstrained_val = get_unconstrained_value(raw_val)

        extraction_item = {
            "id": str(uuid.uuid4()),
            "page_id": target_page_id,
            "field_name": field_name,
            "raw_value": raw_val,  # FR-NRM-05 preserved
            "canonical_value": canonical_val,
            "unconstrained_value": unconstrained_val,
            "bbox": token.bbox.to_dict() if token.bbox else None,
            "engine": ocr_result.engine_id,
            "model_version": ocr_result.model_version,
            "config_version": ocr_result.config_version,
            "token_confidence": token.confidence,
            "calibrated_confidence": None,
            "novelty_score": None,
            "routing_outcome": None,
            "entry_status": "unknown",  # INVARIANT 2: MUST default to unknown, NEVER live!
            "attestation_refs": [],
            "top_k": [candidate.to_dict() for candidate in token.top_k],
        }
        extractions.append(extraction_item)

    return extractions


def _infer_field_name(text: str, doc_type: str | None) -> str:
    """Heuristic field name inference based on text patterns."""
    lower = text.lower()
    if "khasra" in lower or "survey" in lower:
        return "survey_number"
    if "owner" in lower or "nam" in lower or "ramesh" in lower:
        return "owner_name"
    if "area" in lower or "hectare" in lower or "acre" in lower:
        return "area"
    if "share" in lower or "hissa" in lower:
        return "share_fraction"
    return "text_line"
