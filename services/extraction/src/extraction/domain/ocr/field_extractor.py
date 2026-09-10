"""P0 Field Extraction against Document Type Schemas (FR-EXT-01).

Maps raw OCR tokens into contract-compliant `Extraction` dictionary items.
Supports document fields:
- owner_name
- relationship
- survey_number (khasra number)
- khata_number (khatauni number)
- area
- share_fraction
- land_classification
- mutation_reference
- date

Requirements:
1. `raw_value` is preserved unchanged (FR-NRM-05).
2. `canonical_value` produced ONLY when normalization is justified.
3. `unconstrained_value` preserved (FR-OCR-07).
4. `bbox` association preserved.
5. `confidence` preserved.
6. Engine/model/config provenance preserved.
7. Does not fabricate values; unrecognized tokens default to 'text_line'.
8. Missing fields remain missing.
9. Document-type-aware extraction where supported.
10. Deterministic and testable.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from .interfaces import OCRResult
from .normalization import get_unconstrained_value, normalize_field_canonical


def extract_fields_for_doctype(
    ocr_result: OCRResult,
    page_id: str | None = None,
    doc_type: str | None = None,
) -> list[dict[str, Any]]:
    """Converts an OCRResult into a list of Extraction dicts matching `extraction.schema.json`."""
    extractions: list[dict[str, Any]] = []
    target_page_id = page_id or ocr_result.page_id

    for token in ocr_result.tokens:
        raw_val = token.text
        if not raw_val or not raw_val.strip():
            continue

        field_name = classify_field_name(raw_val, doc_type)
        canonical_val = normalize_field_canonical(field_name, raw_val)
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


def classify_field_name(text: str, doc_type: str | None = None) -> str:
    """Deterministic, pattern-matched field classification with doc_type context awareness."""
    lower = text.lower().strip()

    # Explicit keyword matches take highest precedence
    if re.search(r"\b(?:mutation|intakal|dakhil\s*kharij|order\s*ref)\b", lower):
        return "mutation_reference"

    if re.search(r"\b(?:date|dated|scanning\s*date)\b", lower) or re.search(r"\b\d{2}[/.-]\d{2}[/.-]\d{4}\b", lower):
        return "date"

    if re.search(r"\b(?:s/o|w/o|d/o|c/o|son\s+of|wife\s+of|daughter\s+of|care\s+of|late)\b", lower):
        return "relationship"

    if re.search(r"\b(?:khasra|survey|plot|gata)\b", lower):
        return "survey_number"

    if re.search(r"\b(?:khatauni|khata|account|khewat)\b", lower):
        return "khata_number"

    if re.search(r"\b(?:area|hectare|ha|acre|bigha|biswa|sq\s*m|square\s*meter)\b", lower):
        return "area"

    if re.search(r"\b(?:classification|land\s*type|chahi|barani|irrigated|unirrigated|abadi|agricultural)\b", lower):
        return "land_classification"

    if re.search(r"\b(?:share|hissa|fraction)\b", lower):
        return "share_fraction"

    if re.search(r"\b(?:owner|khatedar|pattadar|landowner|nam|name|shri|smt)\b", lower):
        return "owner_name"

    # Contextual disambiguation for numbers like "12/34" based on doc_type
    if re.search(r"^\d+/\d+$", lower):
        if doc_type in ("ror", "khasra_khatauni"):
            return "survey_number"
        if doc_type == "jamabandi":
            return "share_fraction"

    if re.search(r"^\d+$", lower) and doc_type == "jamabandi":
        return "khata_number"

    # Default fallback: Unclassified text line (no fabrication or guessing)
    return "text_line"
