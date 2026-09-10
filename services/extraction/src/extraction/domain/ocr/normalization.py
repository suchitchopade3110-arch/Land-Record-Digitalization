"""Text Normalization & Gazetteer Pre-reading (FR-NRM-05, FR-OCR-07)."""
from __future__ import annotations

import re


def normalize_text(raw_value: str) -> str | None:
    """Cleans and standardizes raw text into `canonical_value`.
    IMPORTANT: `raw_value` is never modified or overwritten (FR-NRM-05).
    """
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    # Basic whitespace & punctuation cleanup
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def get_unconstrained_value(raw_value: str) -> str | None:
    """Captures unconstrained OCR reading before gazetteer re-ranking (FR-OCR-07)."""
    return raw_value.strip() if raw_value else None
