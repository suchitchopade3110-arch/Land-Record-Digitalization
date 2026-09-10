"""Baidu Unlimited-OCR Engine Adapter.

Implements `OCREngineAdapter` for Baidu Unlimited-OCR (self-hosted VLM or Baidu Cloud REST API).
Encapsulates all Baidu-specific client calls, token parsing, and location coordinate transforms
behind the normalized domain interface.
"""
from __future__ import annotations

import base64
import os
import re

from .interfaces import (
    BoundingBox,
    OCRCandidate,
    OCRConfigurationError,
    OCREngineAdapter,
    OCRProcessingError,
    OCRResponseParseError,
    OCRResult,
    OCRToken,
)


class BaiduUnlimitedOCRAdapter(OCREngineAdapter):
    """Adapter for Baidu Unlimited-OCR inference engine."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        api_key: str | None = None,
        secret_key: str | None = None,
        model_version: str = "baidu-unlimited-v1",
        allow_test_fallback: bool = False,
    ):
        self._endpoint_url = endpoint_url or os.environ.get("BAIDU_OCR_ENDPOINT")
        self._api_key = api_key or os.environ.get("BAIDU_OCR_API_KEY")
        self._secret_key = secret_key or os.environ.get("BAIDU_OCR_SECRET_KEY")
        self._model_version = model_version
        self._allow_test_fallback = allow_test_fallback

    @property
    def engine_id(self) -> str:
        return "printed_ocr"

    @property
    def model_version(self) -> str:
        return self._model_version

    def process_image(
        self,
        image_bytes: bytes,
        page_id: str,
        config_version: str = "v1",
    ) -> OCRResult:
        """Process image bytes through Baidu Unlimited-OCR and return a normalized OCRResult.

        Page coordinates are isolated strictly to `page_id` without mixing across pages.
        """
        if not image_bytes:
            raise OCRProcessingError("Image payload is empty")

        if not self._endpoint_url:
            if self._allow_test_fallback:
                return self._fallback_mock_extract(page_id, config_version)
            raise OCRConfigurationError(
                "Baidu Unlimited-OCR endpoint is not configured (BAIDU_OCR_ENDPOINT missing)"
            )

        return self._call_baidu_api(image_bytes, page_id, config_version)

    def _call_baidu_api(self, image_bytes: bytes, page_id: str, config_version: str) -> OCRResult:
        import httpx

        headers = {"User-Agent": "LandRecord-ExtractionService/0.1"}

        if self._api_key:
            headers["X-API-Key"] = self._api_key

        try:
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            payload_data = {"image": b64_img, "page_id": page_id}

            response = httpx.post(
                self._endpoint_url,  # type: ignore[arg-type]
                json=payload_data,
                headers=headers,
                timeout=15.0,
            )
            response.raise_for_status()
        except Exception as err:
            sanitized_err = self._sanitize_secrets(str(err))
            raise OCRProcessingError(f"Baidu OCR call failed: {sanitized_err}") from None

        try:
            data = response.json()
        except Exception as err:
            sanitized_err = self._sanitize_secrets(str(err))
            raise OCRResponseParseError(f"Failed to parse JSON response from Baidu OCR: {sanitized_err}") from None

        return self.parse_baidu_response(data, page_id=page_id, config_version=config_version)

    def parse_baidu_response(self, data: dict, page_id: str, config_version: str = "v1") -> OCRResult:
        """Parses raw Baidu Unlimited-OCR or Baidu AIP JSON response into normalized `OCRResult`."""
        if not isinstance(data, dict):
            raise OCRResponseParseError("Response body must be a JSON object")

        if "error_code" in data and data["error_code"] != 0:
            err_msg = self._sanitize_secrets(str(data.get("error_msg", "Unknown error")))
            raise OCRProcessingError(f"Baidu OCR API error code {data['error_code']}: {err_msg}")

        # Check for Baidu Unlimited-OCR VLM / REST response items
        raw_items = data.get("words_result") or data.get("tokens") or data.get("predictions")
        if raw_items is None or not isinstance(raw_items, list):
            raise OCRResponseParseError("Response missing valid 'words_result', 'tokens', or 'predictions' array")

        tokens: list[OCRToken] = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                raise OCRResponseParseError(f"Malformed token item at index {idx}")

            text = item.get("words") or item.get("text") or item.get("label")
            if text is None or not isinstance(text, str):
                raise OCRResponseParseError(f"Missing text string in token at index {idx}")

            prob = item.get("probability") or item.get("confidence") or item.get("score")
            confidence = 0.95
            if isinstance(prob, dict):
                confidence = float(prob.get("average", 0.95))
            elif isinstance(prob, (int, float)):
                confidence = float(prob)

            # Location / BoundingBox extraction
            bbox = self._parse_location(item, idx)

            top_k = [OCRCandidate(value=text, confidence=confidence)]
            tokens.append(OCRToken(text=text, confidence=confidence, bbox=bbox, top_k=top_k))

        full_text = "\n".join(t.text for t in tokens)
        return OCRResult(
            page_id=page_id,
            tokens=tokens,
            full_text=full_text,
            engine_id=self.engine_id,
            model_version=self.model_version,
            config_version=config_version,
        )

    def _parse_location(self, item: dict, idx: int) -> BoundingBox | None:
        """Parses location dictionary or box coordinate array without losing position fidelity."""
        loc = item.get("location") or item.get("bbox") or item.get("box")
        if not loc:
            return None

        if isinstance(loc, dict):
            try:
                return BoundingBox(
                    x=float(loc.get("left", loc.get("x", 0.0))),
                    y=float(loc.get("top", loc.get("y", 0.0))),
                    w=float(loc.get("width", loc.get("w", 0.0))),
                    h=float(loc.get("height", loc.get("h", 0.0))),
                )
            except (ValueError, TypeError) as err:
                raise OCRResponseParseError(f"Invalid numeric location values at index {idx}: {err}") from err

        if isinstance(loc, (list, tuple)) and len(loc) >= 4:
            try:
                # [x, y, w, h] or [ymin, xmin, ymax, xmax]
                x = float(loc[0])
                y = float(loc[1])
                w = float(loc[2])
                h = float(loc[3])
                return BoundingBox(x=x, y=y, w=w, h=h)
            except (ValueError, TypeError) as err:
                raise OCRResponseParseError(f"Invalid numeric coordinate box array at index {idx}: {err}") from err

        return None

    def _sanitize_secrets(self, text: str) -> str:
        """Redacts sensitive credentials or API keys from error messages."""
        if self._api_key:
            text = text.replace(self._api_key, "[REDACTED_API_KEY]")
        if self._secret_key:
            text = text.replace(self._secret_key, "[REDACTED_SECRET_KEY]")
        return re.sub(r"(access_token|api_key|secret_key)=[^& ]+", r"\1=[REDACTED]", text, flags=re.IGNORECASE)

    def _fallback_mock_extract(self, page_id: str, config_version: str) -> OCRResult:
        """Synthetic mock response for dev/test mode."""
        tokens = [
            OCRToken(
                text="Sample Land Record",
                confidence=0.99,
                bbox=BoundingBox(x=10.0, y=10.0, w=100.0, h=20.0),
                top_k=[OCRCandidate(value="Sample Land Record", confidence=0.99)],
            )
        ]
        return OCRResult(
            page_id=page_id,
            tokens=tokens,
            full_text="Sample Land Record",
            engine_id=self.engine_id,
            model_version=self.model_version,
            config_version=config_version,
        )
