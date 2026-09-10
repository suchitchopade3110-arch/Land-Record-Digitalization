"""OCR engine domain package: adapters, field extractors, normalization, table processing, pipeline."""

from .baidu_unlimited import BaiduUnlimitedOCRAdapter
from .field_extractor import extract_fields_for_doctype
from .hwr_adapter import HWRAdapter
from .interfaces import BoundingBox, OCREngineAdapter, OCRResult, OCRToken
from .normalization import get_unconstrained_value, normalize_text
from .pipeline import process_page_text, process_page_text_with_relationships
from .region_router import LogicalRegion, LogicalRoute, PageRoutingPlan, RegionDispatcher, RegionRouter
from .relationship_extractor import OwnerParcelShareBinding, extract_relationships
from .table_extractor import RuledTableExtractor, TableCell, TableStructure, extract_table_cells

__all__ = [
    "BaiduUnlimitedOCRAdapter",
    "BoundingBox",
    "HWRAdapter",
    "LogicalRegion",
    "LogicalRoute",
    "OCREngineAdapter",
    "OCRResult",
    "OCRToken",
    "OwnerParcelShareBinding",
    "PageRoutingPlan",
    "RegionDispatcher",
    "RegionRouter",
    "RuledTableExtractor",
    "TableCell",
    "TableStructure",
    "extract_fields_for_doctype",
    "extract_relationships",
    "extract_table_cells",
    "get_unconstrained_value",
    "normalize_text",
    "process_page_text",
    "process_page_text_with_relationships",
]
