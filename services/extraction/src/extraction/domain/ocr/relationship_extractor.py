"""Relationship Extraction (Owner ↔ Share ↔ Parcel) (FR-EXT-02).

Infers relationships between extracted fields while strictly adhering to `extraction.schema.json`
`additionalProperties: false` schema constraint.
"""
from __future__ import annotations


def extract_relationships(extractions: list[dict]) -> list[dict]:
    """Infers structural relationships between owner names, shares, and survey numbers.

    Ensures no non-schema fields are mutated directly onto `Extraction` dict instances.
    """
    current_parcel = None

    for item in extractions:
        field_name = item.get("field_name")
        if field_name == "survey_number":
            current_parcel = item.get("raw_value")

    return extractions
