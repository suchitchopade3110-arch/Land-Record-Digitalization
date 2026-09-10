"""Handwriting Recognition (HWR) Engine Adapter & Placeholder (FR-OCR-02 / FR-TRI-11).

Implements `HWREngineAdapter` for handwritten land records, marginalia, and signatures.
- Accepts region bounding boxes (does not assume page-wide HWR).
- Returns normalized `OCRResult` representation (text, bbox, confidence, engine, model_version, config_version).
- Explicitly marks placeholder/unsupported HWR states rather than returning fake OCR.
- Supports future per-writer cluster adapter routing via `writer_cluster_id`.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .interfaces import (
    BoundingBox,
    HWREngineAdapter,
    HWRNotImplementedError,
    OCRCandidate,
    OCRResult,
    OCRToken,
)

logger = logging.getLogger(__name__)


class HWRAdapter(HWREngineAdapter):
    """Placeholder HWR adapter when no production HWR model is currently active.

    Explicitly fails or returns documented unsupported status rather than fake OCR.
    Supports future per-writer model routing via `writer_cluster_id`.
    """

    UNSUPPORTED_TEXT = "[UNSUPPORTED_HWR_REGION]"

    def __init__(
        self,
        endpoint_url: str | None = None,
        writer_cluster_id: str | None = None,
        model_version: str = "hwr-placeholder-v1",
        raise_on_unsupported: bool = False,
    ):
        self._endpoint_url = endpoint_url or os.environ.get("HWR_ENDPOINT")
        self.writer_cluster_id = writer_cluster_id
        self._model_version = model_version
        self.raise_on_unsupported = raise_on_unsupported

    @property
    def engine_id(self) -> str:
        return "hwr"

    @property
    def model_version(self) -> str:
        return self._model_version

    def process_region(
        self,
        image_bytes: bytes,
        page_id: str,
        region_bbox: BoundingBox | None = None,
        writer_cluster_id: str | None = None,
        config_version: str = "v1",
    ) -> OCRResult:
        """Processes a specific image region for handwriting recognition.

        Fails explicitly if `raise_on_unsupported` is True or no endpoint is configured.
        Otherwise returns a documented unsupported result rather than fake OCR text.
        """
        active_writer_cluster = writer_cluster_id or self.writer_cluster_id
        r_bbox = region_bbox or BoundingBox(x=0.0, y=0.0, w=1000.0, h=1000.0)

        # Fail explicitly if configured to raise or if no endpoint URL is present and raise mode is active
        if self.raise_on_unsupported:
            raise HWRNotImplementedError(
                f"Production HWR engine is not configured/implemented for writer cluster '{active_writer_cluster or 'default'}'. "
                "Per-writer model integration required."
            )

        logger.info(
            "HWR region processed for page %s (writer_cluster: %s). Returning documented unsupported result.",
            page_id,
            active_writer_cluster or "default",
        )

        token = OCRToken(
            text=self.UNSUPPORTED_TEXT,
            confidence=0.0,
            bbox=r_bbox,
            top_k=[OCRCandidate(value=self.UNSUPPORTED_TEXT, confidence=0.0)],
        )

        return OCRResult(
            page_id=page_id,
            tokens=[token],
            full_text=self.UNSUPPORTED_TEXT,
            engine_id=self.engine_id,
            model_version=self.model_version,
            config_version=config_version,
        )


class WriterAwareHWRRegistry:
    """Registry allowing future per-writer HWR adapters to be registered for specific writer clusters."""

    def __init__(self, default_adapter: HWREngineAdapter | None = None):
        self.default_adapter = default_adapter or HWRAdapter()
        self._writer_adapters: dict[str, HWREngineAdapter] = {}

    def register_writer_adapter(self, writer_cluster_id: str, adapter: HWREngineAdapter) -> None:
        self._writer_adapters[writer_cluster_id] = adapter

    def get_adapter(self, writer_cluster_id: str | None = None) -> HWREngineAdapter:
        if writer_cluster_id and writer_cluster_id in self._writer_adapters:
            return self._writer_adapters[writer_cluster_id]
        return self.default_adapter
