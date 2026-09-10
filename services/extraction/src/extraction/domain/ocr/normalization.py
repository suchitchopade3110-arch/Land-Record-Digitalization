"""Text Normalization & Gazetteer Pre-reading (FR-NRM-05, FR-OCR-07)."""
from __future__ import annotations

import re


def normalize_text(raw_value: str) -> str | None:
    """Cleans whitespace while preserving raw string contents.

    IMPORTANT: `raw_value` is never modified or overwritten in extraction records (FR-NRM-05).
    """
    if not raw_value:
        return None
    cleaned = raw_value.strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def get_unconstrained_value(raw_value: str) -> str | None:
    """Captures unconstrained OCR reading before gazetteer re-ranking (FR-OCR-07)."""
    return raw_value.strip() if raw_value else None


def normalize_field_canonical(field_name: str, raw_value: str) -> str | None:
    """Produces `canonical_value` ONLY when field-specific normalization is justified.

    Otherwise returns cleanly whitespace-normalized string.
    """
    if not raw_value:
        return None

    cleaned = raw_value.strip()

    if field_name == "survey_number":
        # Extract pure survey number e.g. "Khasra No 123/4" -> "123/4"
        match = re.search(r"(?:khasra|survey|plot|gata)\s*(?:no\.?|num|#)?\s*([0-9a-zA-Z/\-]+)", cleaned, re.IGNORECASE)
        if match:
            return match.group(1)

    elif field_name == "khata_number":
        # Extract khata number e.g. "Khata No 56" -> "56"
        match = re.search(r"(?:khatauni|khata|account)\s*(?:no\.?|num|#)?\s*([0-9a-zA-Z/\-]+)", cleaned, re.IGNORECASE)
        if match:
            return match.group(1)

    elif field_name == "share_fraction":
        # Extract share fraction e.g. "Share: 1/2" -> "1/2"
        match = re.search(r"([0-9]+/[0-9]+)", cleaned)
        if match:
            return match.group(1)

    elif field_name == "area":
        # Standardize area unit string e.g. "Area: 1.25 Hectare" -> "1.25 hectare"
        match = re.search(r"([0-9.]+\s*(?:hectare|ha|acre|bigha|biswa|sq\.?\s*m|square meters?))", cleaned, re.IGNORECASE)
        if match:
            return match.group(1).lower()

    elif field_name == "mutation_reference":
        # Extract mutation ref e.g. "Mutation No 405" -> "405"
        match = re.search(r"(?:mutation|intakal|order)\s*(?:no\.?|ref|#)?\s*([0-9a-zA-Z/\-]+)", cleaned, re.IGNORECASE)
        if match:
            return match.group(1)

    elif field_name == "date":
        # Standardize DD/MM/YYYY to YYYY-MM-DD
        match = re.search(r"(\d{2})[/.-](\d{2})[/.-](\d{4})", cleaned)
        if match:
            day, month, year = match.groups()
            return f"{year}-{month}-{day}"
        match_iso = re.search(r"(\d{4})[/.-](\d{2})[/.-](\d{2})", cleaned)
        if match_iso:
            year, month, day = match_iso.groups()
            return f"{year}-{month}-{day}"

    # Default fallback: whitespace-cleaned string
    return re.sub(r"\s+", " ", cleaned)
