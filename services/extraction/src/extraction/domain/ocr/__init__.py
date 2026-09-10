"""OCR engine domain package: adapters, field extractors, normalization, table processing."""

from .baidu_unlimited import BaiduUnlimitedOCRAdapter
from .field_extractor import extract_fields_for_doctype
from .hwr_adapter import HWRAdapter
from .interfaces import BoundingBox, OCREngineAdapter, OCRResult, OCRToken
from .normalization import get_unconstrained_value, normalize_text
from .relationship_extractor import extract_relationships
from .table_extractor import extract_table_cells

__all__ = [
    "BaiduUnlimitedOCRAdapter",
    "BoundingBox",
    "HWRAdapter",
    "OCREngineAdapter",
    "OCRResult",
    "OCRToken",
    "extract_fields_for_doctype",
    "extract_relationships",
    "extract_table_cells",
    "get_unconstrained_value",
    "normalize_text",
]
