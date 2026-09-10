"""Domain interfaces and normalized data structures for OCR engines."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class OCRError(Exception):
    """Base exception for OCR engine operations."""


class OCRConfigurationError(OCRError):
    """Raised when engine configuration, endpoint, or credentials are missing or invalid."""


class OCRProcessingError(OCRError):
    """Raised when the OCR engine fails during image inference or network transport."""


class OCRResponseParseError(OCRError):
    """Raised when the raw engine output cannot be parsed into a normalized result."""


@dataclass(frozen=True)
class BoundingBox:
    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass(frozen=True)
class OCRCandidate:
    value: str
    confidence: float

    def to_dict(self) -> dict[str, float | str]:
        return {"value": self.value, "confidence": self.confidence}


@dataclass
class OCRToken:
    text: str
    confidence: float
    bbox: BoundingBox | None = None
    top_k: list[OCRCandidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "top_k": [candidate.to_dict() for candidate in self.top_k],
        }


@dataclass
class OCRResult:
    """Normalized OCR result representation satisfying pipeline requirement 3."""

    page_id: str
    tokens: list[OCRToken]
    full_text: str
    engine_id: str  # "printed_ocr" | "hwr" | "table_extractor"
    model_version: str
    config_version: str


class OCREngineAdapter(ABC):
    """Abstract adapter interface for isolating OCR model implementations.

    Guarantees Baidu Unlimited-OCR or alternative engines (e.g. Tesseract, PaddleOCR)
    can be swapped without altering upstream workers or domain logic.
    """

    @property
    @abstractmethod
    def engine_id(self) -> str:
        """Returns engine identifier enum required by Extraction contract."""

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Returns active model version string."""

    @abstractmethod
    def process_image(
        self,
        image_bytes: bytes,
        page_id: str,
        config_version: str = "v1",
    ) -> OCRResult:
        """Process image bytes and return normalized OCRResult with tokens and bounding boxes."""
