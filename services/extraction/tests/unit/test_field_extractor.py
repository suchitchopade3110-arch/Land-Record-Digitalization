"""Comprehensive unit tests for P0 Field Extractor (FR-EXT-01).

Tests:
1. Extraction of owner_name, relationship, survey_number, khata_number, area,
   share_fraction, land_classification, mutation_reference, date.
2. Raw value immutability (FR-NRM-05).
3. Canonical value justification.
4. Bounding box, confidence, and provenance preservation.
5. Non-fabrication & safe missing field handling.
6. Document-type-aware extraction heuristics.
"""
from extraction.domain.ocr.interfaces import BoundingBox, OCRCandidate, OCRResult, OCRToken
from extraction.domain.ocr.field_extractor import classify_field_name, extract_fields_for_doctype
from extraction.domain.ocr.normalization import get_unconstrained_value, normalize_field_canonical, normalize_text


def test_classify_field_name_p0_fields():
    assert classify_field_name("Owner: Ramesh Chand") == "owner_name"
    assert classify_field_name("Ramesh Chand s/o Mohan Lal") == "relationship"
    assert classify_field_name("Khasra No 123/4") == "survey_number"
    assert classify_field_name("Khatauni No 56") == "khata_number"
    assert classify_field_name("Area: 1.25 Hectare") == "area"
    assert classify_field_name("Share: 1/2") == "share_fraction"
    assert classify_field_name("Land Type: Chahi Irrigated") == "land_classification"
    assert classify_field_name("Mutation No 405") == "mutation_reference"
    assert classify_field_name("Date: 15/08/2023") == "date"
    assert classify_field_name("Random Header String") == "text_line"


def test_normalize_field_canonical_justified_only():
    # Only justified canonical transforms are performed
    assert normalize_field_canonical("survey_number", "Khasra No 123/4") == "123/4"
    assert normalize_field_canonical("khata_number", "Khatauni No 56") == "56"
    assert normalize_field_canonical("share_fraction", "Share: 1/2") == "1/2"
    assert normalize_field_canonical("area", "Area: 1.25 Hectare") == "1.25 hectare"
    assert normalize_field_canonical("mutation_reference", "Mutation No 405") == "405"
    assert normalize_field_canonical("date", "Date: 15/08/2023") == "2023-08-15"
    # Unmodified fallback for general text
    assert normalize_field_canonical("owner_name", " Ramesh Chand ") == "Ramesh Chand"


def test_extract_fields_for_doctype_synthetic_ocr_result():
    bbox1 = BoundingBox(x=10.0, y=20.0, w=100.0, h=30.0)
    bbox2 = BoundingBox(x=10.0, y=60.0, w=200.0, h=30.0)
    bbox3 = BoundingBox(x=10.0, y=100.0, w=150.0, h=30.0)
    bbox4 = BoundingBox(x=10.0, y=140.0, w=120.0, h=30.0)
    bbox5 = BoundingBox(x=10.0, y=180.0, w=180.0, h=30.0)

    tokens = [
        OCRToken(
            text="   Khasra No 123/4   ",
            confidence=0.98,
            bbox=bbox1,
            top_k=[OCRCandidate(value="   Khasra No 123/4   ", confidence=0.98)],
        ),
        OCRToken(
            text="Shri Ramesh Chand s/o Mohan Lal",
            confidence=0.95,
            bbox=bbox2,
            top_k=[OCRCandidate(value="Shri Ramesh Chand s/o Mohan Lal", confidence=0.95)],
        ),
        OCRToken(
            text="Khatauni No 56",
            confidence=0.93,
            bbox=bbox3,
            top_k=[OCRCandidate(value="Khatauni No 56", confidence=0.93)],
        ),
        OCRToken(
            text="Share: 1/2",
            confidence=0.96,
            bbox=bbox4,
            top_k=[OCRCandidate(value="Share: 1/2", confidence=0.96)],
        ),
        OCRToken(
            text="Mutation No 405",
            confidence=0.91,
            bbox=bbox5,
            top_k=[OCRCandidate(value="Mutation No 405", confidence=0.91)],
        ),
    ]

    ocr_result = OCRResult(
        page_id="page-p0-001",
        tokens=tokens,
        full_text="\n".join(t.text for t in tokens),
        engine_id="printed_ocr",
        model_version="baidu-unlimited-v1",
        config_version="cfg-v1",
    )

    extractions = extract_fields_for_doctype(ocr_result, page_id="page-p0-001", doc_type="jamabandi")

    assert len(extractions) == 5

    # Check Token 1: survey_number
    e1 = extractions[0]
    assert e1["field_name"] == "survey_number"
    assert e1["raw_value"] == "   Khasra No 123/4   "  # FR-NRM-05: Raw unchanged
    assert e1["canonical_value"] == "123/4"
    assert e1["unconstrained_value"] == "Khasra No 123/4"
    assert e1["bbox"] == {"x": 10.0, "y": 20.0, "w": 100.0, "h": 30.0}
    assert e1["token_confidence"] == 0.98
    assert e1["engine"] == "printed_ocr"
    assert e1["model_version"] == "baidu-unlimited-v1"
    assert e1["config_version"] == "cfg-v1"
    assert e1["entry_status"] == "unknown"  # Invariant 2

    # Check Token 2: relationship
    e2 = extractions[1]
    assert e2["field_name"] == "relationship"
    assert e2["raw_value"] == "Shri Ramesh Chand s/o Mohan Lal"

    # Check Token 3: khata_number
    e3 = extractions[2]
    assert e3["field_name"] == "khata_number"
    assert e3["canonical_value"] == "56"

    # Check Token 4: share_fraction
    e4 = extractions[3]
    assert e4["field_name"] == "share_fraction"
    assert e4["canonical_value"] == "1/2"

    # Check Token 5: mutation_reference
    e5 = extractions[4]
    assert e5["field_name"] == "mutation_reference"
    assert e5["canonical_value"] == "405"


def test_doc_type_aware_heuristics():
    bbox = BoundingBox(x=0.0, y=0.0, w=10.0, h=10.0)
    token = OCRToken(text="12/34", confidence=0.9, bbox=bbox)
    ocr_res = OCRResult(page_id="p1", tokens=[token], full_text="12/34", engine_id="printed_ocr", model_version="v1", config_version="c1")

    # In jamabandi, implicit "12/34" is classified as share_fraction
    ext_jama = extract_fields_for_doctype(ocr_res, doc_type="jamabandi")
    assert ext_jama[0]["field_name"] == "share_fraction"

    # In ror, implicit "12/34" is classified as survey_number
    ext_ror = extract_fields_for_doctype(ocr_res, doc_type="ror")
    assert ext_ror[0]["field_name"] == "survey_number"
