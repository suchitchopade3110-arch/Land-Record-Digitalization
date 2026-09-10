"""Unit tests for field extraction and normalization (FR-EXT-01, FR-NRM-05, FR-OCR-07)."""
from extraction.domain.ocr.baidu_unlimited import BaiduUnlimitedOCRAdapter
from extraction.domain.ocr.field_extractor import extract_fields_for_doctype
from extraction.domain.ocr.normalization import get_unconstrained_value, normalize_text


def test_normalize_text_preserves_raw_value():
    raw = "   Khasra  123/4   "
    canonical = normalize_text(raw)
    assert canonical == "Khasra 123/4"
    assert raw == "   Khasra  123/4   "  # Unaltered


def test_get_unconstrained_value():
    assert get_unconstrained_value("Survey 99") == "Survey 99"


def test_extract_fields_for_doctype_default_entry_status():
    adapter = BaiduUnlimitedOCRAdapter(allow_test_fallback=True)
    ocr_result = adapter.process_image(b"fake_bytes", page_id="page-123")
    extractions = extract_fields_for_doctype(ocr_result, page_id="page-123", doc_type="ror")

    assert len(extractions) > 0
    for item in extractions:
        # Invariant 2: MUST default to unknown, NEVER live!
        assert item["entry_status"] == "unknown"
        assert item["raw_value"] is not None
        assert "engine" in item
        assert item["engine"] == "printed_ocr"
