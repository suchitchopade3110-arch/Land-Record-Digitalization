"""Unit tests for record assembly (FR-EXT-04)."""
import pytest
from extraction.domain.record_assembly import assemble


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
