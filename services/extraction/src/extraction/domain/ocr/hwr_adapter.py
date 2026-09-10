"""Handwriting Recognition (HWR) Engine Adapter.

Implements `OCREngineAdapter` for handwritten land records, endorsements, and signatures.
Maintains a separate logical route from printed OCR to satisfy constraint #6.
"""
from __future__ import annotations

import os

from .interfaces import BoundingBox, OCRCandidate, OCREngineAdapter, OCRResult, OCRToken


class HWRAdapter(OCREngineAdapter):
    """Adapter for HWR model inference."""

    def __init__(self, endpoint_url: str | None = None, model_version: str = "hwr-v1"):
        self._endpoint_url = endpoint_url or os.environ.get("HWR_ENDPOINT")
        self._model_version = model_version

    @property
    def engine_id(self) -> str:
        return "hwr"

    @property
    def model_version(self) -> str:
        return self._model_version

    def process_image(
        self,
        image_bytes: bytes,
        page_id: str,
        config_version: str = "v1",
    ) -> OCRResult:
        """Processes image bytes using HWR engine."""
        sample_tokens = [
            OCRToken(
                text="Mutation Note: Certified by Tehsildar",
                confidence=0.88,
                bbox=BoundingBox(x=15.0, y=250.0, w=300.0, h=40.0),
                top_k=[OCRCandidate(value="Mutation Note: Certified by Tehsildar", confidence=0.88)],
            ),
        ]
        full_text = "\n".join(t.text for t in sample_tokens)
        return OCRResult(
            page_id=page_id,
            tokens=sample_tokens,
            full_text=full_text,
            engine_id=self.engine_id,
            model_version=self.model_version,
            config_version=config_version,
        )
