"""Unit tests for M3 OCR End-to-End Vertical Slice Pipeline.

Tests:
1. Normal page extraction
2. Empty page (no image_bytes or 0 tokens returned)
3. Malformed OCR result handling (fails safely returning [])
4. Multiple OCR regions
5. Bounding box preservation ({x, y, w, h})
6. Confidence preservation (token_confidence)
7. Provenance preservation (engine, model_version, config_version, raw_value, canonical_value, unconstrained_value)
"""
from unittest.mock import MagicMock

import pytest
from extraction.domain.ocr.baidu_unlimited import BaiduUnlimitedOCRAdapter
from extraction.domain.ocr.interfaces import (
    BoundingBox,
    OCRCandidate,
    OCRProcessingError,
    OCRResult,
    OCRToken,
)
from extraction.domain.ocr.pipeline import process_page_text


# 1. Normal Page Extraction & Multiple OCR Regions
def test_normal_page_vertical_slice_extraction():
    payload = {
        "page_id": "page-normal-001",
        "document_id": "doc-001",
        "doc_type": "ror",
        "image_bytes": b"normal_page_image_data",
    }
    work_envelope = {"config_version": "cfg-v10"}

    mock_adapter = MagicMock()
    mock_adapter.engine_id = "printed_ocr"
    mock_adapter.model_version = "baidu-unlimited-v1"

    token1 = OCRToken(
        text="Khasra No 100",
        confidence=0.98,
        bbox=BoundingBox(x=12.0, y=34.0, w=150.0, h=25.0),
        top_k=[OCRCandidate(value="Khasra No 100", confidence=0.98)],
    )
    token2 = OCRToken(
        text="Owner: Anita Devi",
        confidence=0.94,
        bbox=BoundingBox(x=12.0, y=70.0, w=210.0, h=25.0),
        top_k=[OCRCandidate(value="Owner: Anita Devi", confidence=0.94)],
    )

    mock_adapter.process_image.return_value = OCRResult(
        page_id="page-normal-001",
        tokens=[token1, token2],
        full_text="Khasra No 100\nOwner: Anita Devi",
        engine_id="printed_ocr",
        model_version="baidu-unlimited-v1",
        config_version="cfg-v10",
    )

    extractions = process_page_text(payload, work_envelope=work_envelope, ocr_adapter=mock_adapter)

    # Test multiple regions extracted
    assert len(extractions) == 2

    ext1 = extractions[0]
    ext2 = extractions[1]

    # 5. Bounding Box Preservation
    assert ext1["bbox"] == {"x": 12.0, "y": 34.0, "w": 150.0, "h": 25.0}
    assert ext2["bbox"] == {"x": 12.0, "y": 70.0, "w": 210.0, "h": 25.0}

    # 6. Confidence Preservation
    assert ext1["token_confidence"] == 0.98
    assert ext2["token_confidence"] == 0.94

    # 7. Provenance Preservation
    assert ext1["engine"] == "printed_ocr"
    assert ext1["model_version"] == "baidu-unlimited-v1"
    assert ext1["config_version"] == "cfg-v10"

    # Raw value vs canonical & unconstrained preservation
    assert ext1["raw_value"] == "Khasra No 100"
    assert ext1["canonical_value"] == "100"
    assert ext1["unconstrained_value"] == "Khasra No 100"

    # Invariant 2 check
    assert ext1["entry_status"] == "unknown"
    assert ext2["entry_status"] == "unknown"


# 2. Empty Page Test
def test_empty_page_vertical_slice_returns_empty_list():
    # Scenario A: Missing image_bytes
    payload_no_bytes = {"page_id": "page-empty-001", "image_bytes": None}
    res_no_bytes = process_page_text(payload_no_bytes)
    assert res_no_bytes == []

    # Scenario B: OCR adapter returns 0 tokens
    mock_adapter = MagicMock()
    mock_adapter.process_image.return_value = OCRResult(
        page_id="page-empty-002",
        tokens=[],
        full_text="",
        engine_id="printed_ocr",
        model_version="baidu-unlimited-v1",
        config_version="v1",
    )
    payload_empty = {"page_id": "page-empty-002", "image_bytes": b"empty_page_bytes"}
    res_empty = process_page_text(payload_empty, ocr_adapter=mock_adapter)
    assert res_empty == []


# 3. Malformed / Failed OCR Result Test
def test_malformed_or_failed_ocr_fails_safely():
    mock_adapter = MagicMock()
    mock_adapter.process_image.side_effect = OCRProcessingError("Baidu API call failed: Connection refused")

    payload = {"page_id": "page-failed-001", "image_bytes": b"valid_bytes"}

    # Fails safely returning [] without raising unhandled exception
    extractions = process_page_text(payload, ocr_adapter=mock_adapter)
    assert extractions == []


def test_invalid_payload_fails_safely():
    # Fails safely returning []
    assert process_page_text("invalid string payload") == []  # type: ignore[arg-type]
