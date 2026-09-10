"""Unit tests for multi-page record assembly (FR-EXT-04).

Tests:
1. Single page record
2. Invalid strategy handling
3. Two-page record assembly
4. Continuation page handling
5. Duplicate field merging with provenance
6. Conflicting values conservative preservation (no silent overwrites)
7. Missing page gap detection in page sequence
"""
import uuid
import pytest
from extraction.domain.record_assembly import assemble, assemble_multi_page_record


def _make_ext(field_name: str, raw_value: str, page_id: str = "p1") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "page_id": page_id,
        "field_name": field_name,
        "raw_value": raw_value,
        "canonical_value": raw_value,
        "unconstrained_value": raw_value,
        "bbox": {"x": 10.0, "y": 20.0, "w": 100.0, "h": 20.0},
        "token_confidence": 0.95,
        "entry_status": "unknown",
    }


def test_assemble_single_page():
    extractions = [{"id": "ext-1"}, {"id": "ext-2"}]
    assembly = assemble(extractions, strategy="single_page", rationale="Single page record")

    assert assembly["strategy"] == "single_page"
    assert assembly["extraction_ids"] == ["ext-1", "ext-2"]
    assert assembly["rationale"] == "Single page record"
    assert "id" in assembly
    assert "record_id" in assembly


def test_assemble_invalid_strategy():
    with pytest.raises(ValueError):
        assemble([], strategy="invalid_strategy")


def test_assemble_two_page_record():
    """1. Grouping and assembling a two-page record."""
    p1_exts = [_make_ext("survey_number", "Khasra 100", page_id="page-1")]
    p2_exts = [_make_ext("owner_name", "Anita Devi", page_id="page-2")]

    pages_meta = [{"page_id": "page-1", "index": 1}, {"page_id": "page-2", "index": 2}]

    res = assemble_multi_page_record([p1_exts, p2_exts], pages_metadata=pages_meta)

    assert res.assembly_contract["strategy"] == "continuation"
    assert len(res.assembly_contract["extraction_ids"]) == 2
    assert res.page_order == ["page-1", "page-2"]
    assert "Assembled 2-page record group" in res.assembly_contract["rationale"]


def test_assemble_continuation_page():
    """2. Continuation page auto-detection and strategy selection."""
    p1_exts = [_make_ext("survey_number", "Khasra 45", page_id="page-1")]
    p2_exts = [_make_ext("share_fraction", "1/2", page_id="page-2")]

    pages_meta = [
        {"page_id": "page-1", "index": 1},
        {"page_id": "page-2", "index": 2, "is_continuation": True},
    ]

    res = assemble_multi_page_record([p1_exts, p2_exts], pages_metadata=pages_meta)

    assert res.assembly_contract["strategy"] == "continuation"
    assert res.has_conflicts is False


def test_assemble_duplicate_field():
    """3. Duplicate field merging across pages while preserving provenance."""
    ext1 = _make_ext("owner_name", "Ramesh Chand", page_id="page-1")
    ext2 = _make_ext("owner_name", "Ramesh Chand", page_id="page-2")

    pages_meta = [{"page_id": "page-1", "index": 1}, {"page_id": "page-2", "index": 2}]

    res = assemble_multi_page_record([[ext1], [ext2]], pages_metadata=pages_meta)

    assert res.has_conflicts is False
    assert len(res.assembly_contract["extraction_ids"]) == 2
    assert "owner_name" in res.assembled_fields
    af = res.assembled_fields["owner_name"]
    assert len(af.source_extractions) == 2
    assert "[DUPLICATE]" in res.assembly_contract["rationale"]


def test_assemble_conflicting_values():
    """4. Conflicting OCR readings handled conservatively (both preserved, conflict logged)."""
    ext1 = _make_ext("survey_number", "Khasra 100", page_id="page-1")
    ext2 = _make_ext("survey_number", "Khasra 200", page_id="page-2")

    pages_meta = [{"page_id": "page-1", "index": 1}, {"page_id": "page-2", "index": 2}]

    res = assemble_multi_page_record([[ext1], [ext2]], pages_metadata=pages_meta)

    assert res.has_conflicts is True
    assert len(res.assembly_contract["extraction_ids"]) == 2
    assert "[CONFLICT]" in res.assembly_contract["rationale"]

    af = res.assembled_fields["survey_number"]
    assert af.is_conflicting is True
    assert set(af.conflicting_values) == {"Khasra 100", "Khasra 200"}


def test_assemble_missing_page_gap():
    """5. Missing page gap in sequence detected and logged in rationale."""
    p1_exts = [_make_ext("survey_number", "Khasra 101", page_id="page-1")]
    p3_exts = [_make_ext("owner_name", "Sita Devi", page_id="page-3")]

    pages_meta = [{"page_id": "page-1", "index": 1}, {"page_id": "page-3", "index": 3}]

    res = assemble_multi_page_record([p1_exts, p3_exts], pages_metadata=pages_meta)

    assert res.missing_pages == [2]
    assert "[MISSING_PAGE_GAP]" in res.assembly_contract["rationale"]
