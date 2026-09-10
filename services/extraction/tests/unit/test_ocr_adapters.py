"""Comprehensive unit tests for OCR Foundation & Baidu Unlimited-OCR Adapter.

Tests:
1. Interface behavior
2. Normalization
3. Malformed OCR responses
4. Missing configuration
5. Secret redaction
6. Multi-page coordinate isolation
7. Successful adapter response using mocked data
8. Optional real-engine smoke test
"""
import os
from unittest.mock import MagicMock, patch

import pytest
from extraction.domain.ocr.baidu_unlimited import BaiduUnlimitedOCRAdapter
from extraction.domain.ocr.interfaces import (
    BoundingBox,
    OCRCandidate,
    OCRConfigurationError,
    OCREngineAdapter,
    OCRProcessingError,
    OCRResponseParseError,
    OCRResult,
    OCRToken,
)
from extraction.domain.ocr.normalization import get_unconstrained_value, normalize_text


# 1. Interface Behavior Tests
def test_ocr_engine_adapter_interface_cannot_be_instantiated():
    with pytest.raises(TypeError):
        OCREngineAdapter()  # Abstract class instantiation fails


def test_bounding_box_and_token_structures():
    bbox = BoundingBox(x=10.5, y=20.5, w=100.0, h=50.0)
    assert bbox.to_dict() == {"x": 10.5, "y": 20.5, "w": 100.0, "h": 50.0}

    candidate = OCRCandidate(value="Khasra 45", confidence=0.98)
    assert candidate.to_dict() == {"value": "Khasra 45", "confidence": 0.98}

    token = OCRToken(text="Khasra 45", confidence=0.98, bbox=bbox, top_k=[candidate])
    token_dict = token.to_dict()
    assert token_dict["text"] == "Khasra 45"
    assert token_dict["confidence"] == 0.98
    assert token_dict["bbox"] == {"x": 10.5, "y": 20.5, "w": 100.0, "h": 50.0}


# 2. Normalization Tests
def test_text_normalization():
    raw = "   Survey   Number   100/A  "
    canonical = normalize_text(raw)
    assert canonical == "Survey Number 100/A"
    assert raw == "   Survey   Number   100/A  "  # Raw value is immutable


def test_unconstrained_value():
    assert get_unconstrained_value("   Raw Text 123   ") == "Raw Text 123"
    assert get_unconstrained_value("") is None


# 3. Missing Configuration Tests
def test_missing_configuration_raises_ocr_configuration_error(monkeypatch):
    monkeypatch.delenv("BAIDU_OCR_ENDPOINT", raising=False)
    adapter = BaiduUnlimitedOCRAdapter(endpoint_url=None, allow_test_fallback=False)

    with pytest.raises(OCRConfigurationError) as exc_info:
        adapter.process_image(b"fake_image_bytes", page_id="page-001")

    assert "BAIDU_OCR_ENDPOINT missing" in str(exc_info.value)


# 4. Malformed OCR Responses Tests
def test_malformed_ocr_response_non_dict():
    adapter = BaiduUnlimitedOCRAdapter(endpoint_url="http://mock-baidu:8080/ocr")
    with pytest.raises(OCRResponseParseError) as exc_info:
        adapter.parse_baidu_response("invalid json string", page_id="page-001")  # type: ignore[arg-type]
    assert "Response body must be a JSON object" in str(exc_info.value)


def test_malformed_ocr_response_missing_words_result():
    adapter = BaiduUnlimitedOCRAdapter(endpoint_url="http://mock-baidu:8080/ocr")
    with pytest.raises(OCRResponseParseError) as exc_info:
        adapter.parse_baidu_response({"log_id": 12345}, page_id="page-001")
    assert "missing valid 'words_result'" in str(exc_info.value)


def test_malformed_ocr_response_invalid_words_type():
    adapter = BaiduUnlimitedOCRAdapter(endpoint_url="http://mock-baidu:8080/ocr")
    bad_payload = {"words_result": [{"words": 12345, "probability": {"average": 0.9}}]}
    with pytest.raises(OCRResponseParseError) as exc_info:
        adapter.parse_baidu_response(bad_payload, page_id="page-001")
    assert "Missing text string" in str(exc_info.value)


def test_malformed_ocr_response_invalid_location_values():
    adapter = BaiduUnlimitedOCRAdapter(endpoint_url="http://mock-baidu:8080/ocr")
    bad_payload = {
        "words_result": [
            {
                "words": "Valid Text",
                "location": {"left": "invalid_number", "top": 10},
            }
        ]
    }
    with pytest.raises(OCRResponseParseError) as exc_info:
        adapter.parse_baidu_response(bad_payload, page_id="page-001")
    assert "Invalid numeric location values" in str(exc_info.value)


def test_baidu_api_error_code_raises_processing_error():
    adapter = BaiduUnlimitedOCRAdapter(endpoint_url="http://mock-baidu:8080/ocr")
    error_payload = {"error_code": 216201, "error_msg": "Image format error"}
    with pytest.raises(OCRProcessingError) as exc_info:
        adapter.parse_baidu_response(error_payload, page_id="page-001")
    assert "error code 216201: Image format error" in str(exc_info.value)


# 5. Secret Redaction Test
def test_baidu_adapter_redacts_secrets_in_error_messages():
    secret_key = "super_secret_baidu_key_123"
    adapter = BaiduUnlimitedOCRAdapter(
        endpoint_url="http://mock-baidu:8080/ocr",
        api_key=secret_key,
    )
    with patch("httpx.post", side_effect=Exception(f"Connection error with api_key={secret_key}")):
        with pytest.raises(OCRProcessingError) as exc_info:
            adapter.process_image(b"fake_image", page_id="page-001")

        err_str = str(exc_info.value)
        assert secret_key not in err_str
        assert "[REDACTED" in err_str


# 6. Multi-Page Coordinate Isolation Test
def test_multi_page_documents_retain_page_ids():
    adapter = BaiduUnlimitedOCRAdapter(allow_test_fallback=True)

    page1_res = adapter.process_image(b"page1_bytes", page_id="page-uuid-001")
    page2_res = adapter.process_image(b"page2_bytes", page_id="page-uuid-002")

    assert page1_res.page_id == "page-uuid-001"
    assert page2_res.page_id == "page-uuid-002"
    assert page1_res.page_id != page2_res.page_id


# 7. Successful Adapter Response using Mocked Data
def test_successful_baidu_adapter_response():
    adapter = BaiduUnlimitedOCRAdapter(
        endpoint_url="http://mock-baidu-service:8080/ocr",
        api_key="test-api-key",
    )

    mock_baidu_json = {
        "log_id": 987654321,
        "words_result_num": 2,
        "words_result": [
            {
                "words": "Khasra No 45/2",
                "probability": {"average": 0.97},
                "location": {"left": 10.0, "top": 20.0, "width": 120.0, "height": 25.0},
            },
            {
                "words": "Owner: Suresh Kumar",
                "probability": {"average": 0.94},
                "location": {"left": 10.0, "top": 55.0, "width": 180.0, "height": 25.0},
            },
        ],
    }

    mock_httpx_response = MagicMock()
    mock_httpx_response.status_code = 200
    mock_httpx_response.json.return_value = mock_baidu_json
    mock_httpx_response.raise_for_status.return_value = None

    with patch("httpx.post", return_value=mock_httpx_response) as mock_post:
        result = adapter.process_image(
            image_bytes=b"dummy_image_payload",
            page_id="page-uuid-1234",
            config_version="config-v2",
        )

        assert mock_post.called
        assert mock_post.call_args[0][0] == "http://mock-baidu-service:8080/ocr"
        assert mock_post.call_args[1]["headers"]["X-API-Key"] == "test-api-key"

        assert isinstance(result, OCRResult)
        assert result.page_id == "page-uuid-1234"
        assert result.engine_id == "printed_ocr"
        assert result.model_version == "baidu-unlimited-v1"
        assert result.config_version == "config-v2"
        assert len(result.tokens) == 2
        assert result.tokens[0].text == "Khasra No 45/2"
        assert result.tokens[0].confidence == 0.97
        assert result.tokens[0].bbox.x == 10.0
        assert result.tokens[0].bbox.y == 20.0
        assert result.tokens[0].bbox.w == 120.0
        assert result.tokens[0].bbox.h == 25.0


# 8. Optional Real-Engine Smoke Test
@pytest.mark.skipif(
    not os.environ.get("BAIDU_OCR_ENDPOINT"),
    reason="Optional real-engine smoke test: BAIDU_OCR_ENDPOINT environment variable not set",
)
def test_real_baidu_unlimited_ocr_smoke():
    """Smoke test against a live Baidu Unlimited-OCR service instance.
    Runs only when BAIDU_OCR_ENDPOINT environment variable is explicitly provided.
    """
    adapter = BaiduUnlimitedOCRAdapter()
    sample_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"

    result = adapter.process_image(
        image_bytes=sample_png_bytes,
        page_id="live-smoke-test-page",
        config_version="v1",
    )

    assert isinstance(result, OCRResult)
    assert result.page_id == "live-smoke-test-page"
    assert result.engine_id == "printed_ocr"
