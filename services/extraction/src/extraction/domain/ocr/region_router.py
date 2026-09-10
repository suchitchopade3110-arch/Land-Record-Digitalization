"""M3 Region-Routing Foundation (FR-OCR-04 / FR-TRI-05).

Classifies and routes logical regions of a document page to specialized OCR/processing engines:
1. `printed_text`: Printed Indic/English text -> Baidu Unlimited-OCR / Printed OCR adapter
2. `handwritten_text`: Handwritten text / marginalia -> HWR adapter route (isolated, no page-wide HWR)
3. `ruled_table`: Ruled tables / tabular register grids -> Table Extractor engine route
4. `non_text_map`: Non-text / Cadastral maps / seals -> Bypassed from text OCR (sent to M4 GIS / map pipeline)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .baidu_unlimited import BaiduUnlimitedOCRAdapter
from .hwr_adapter import HWRAdapter
from .interfaces import BoundingBox, OCREngineAdapter, OCRResult, OCRToken
from .table_extractor import extract_table_cells

logger = logging.getLogger(__name__)


class LogicalRoute(str, Enum):
    """Supported logical routes for page region processing."""

    PRINTED_TEXT = "printed_text"
    HANDWRITTEN_TEXT = "handwritten_text"
    RULED_TABLE = "ruled_table"
    NON_TEXT_MAP = "non_text_map"


@dataclass
class LogicalRegion:
    """Represents a bounded, classified region of a page."""

    region_id: str
    route: LogicalRoute
    bbox: BoundingBox
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "route": self.route.value,
            "bbox": self.bbox.to_dict(),
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class PageRoutingPlan:
    """Page-level routing plan containing classified regions and routing metadata."""

    page_id: str
    regions: list[LogicalRegion]
    fallback_applied: bool = False
    dominant_route: LogicalRoute = LogicalRoute.PRINTED_TEXT

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "regions": [r.to_dict() for r in self.regions],
            "fallback_applied": self.fallback_applied,
            "dominant_route": self.dominant_route.value,
        }


class RegionRouter:
    """Analyzes page payloads / layout analysis inputs to route regions to appropriate engines."""

    def route_page(self, page_payload: dict[str, Any]) -> PageRoutingPlan:
        """Determines the routing plan for a page, preserving bounding boxes and route isolation."""
        if not isinstance(page_payload, dict):
            page_payload = {}

        page_id = page_payload.get("page_id") or page_payload.get("id") or "00000000-0000-0000-0000-000000000000"
        explicit_regions = page_payload.get("layout_regions") or page_payload.get("regions") or []

        # 1. Explicit layout regions provided
        if explicit_regions:
            regions: list[LogicalRegion] = []
            for idx, reg in enumerate(explicit_regions):
                r_id = reg.get("id") or f"{page_id}_region_{idx+1}"
                r_type = (reg.get("region_type") or reg.get("type") or reg.get("route") or "").lower()
                is_hw = reg.get("is_handwritten", False)
                is_tbl = reg.get("is_table", False)
                is_map = reg.get("is_map", False)

                # Determine route
                if is_map or r_type in ("map", "cadastral_map", "fmb_sketch", "non_text", "diagram", "seal"):
                    route = LogicalRoute.NON_TEXT_MAP
                elif is_tbl or r_type in ("table", "ruled_table", "tabular_register", "grid"):
                    route = LogicalRoute.RULED_TABLE
                elif is_hw or r_type in ("handwritten", "hwr", "endorsement", "signature"):
                    route = LogicalRoute.HANDWRITTEN_TEXT
                else:
                    route = LogicalRoute.PRINTED_TEXT

                raw_bbox = reg.get("bbox") or {"x": 0.0, "y": 0.0, "w": 1000.0, "h": 1000.0}
                bbox = BoundingBox(
                    x=float(raw_bbox.get("x", 0.0)),
                    y=float(raw_bbox.get("y", 0.0)),
                    w=float(raw_bbox.get("w", 1000.0)),
                    h=float(raw_bbox.get("h", 1000.0)),
                )
                conf = float(reg.get("confidence", 1.0))
                meta = reg.get("metadata") or {}

                regions.append(
                    LogicalRegion(
                        region_id=r_id,
                        route=route,
                        bbox=bbox,
                        confidence=conf,
                        metadata=meta,
                    )
                )

            dominant = self._compute_dominant_route(regions)
            return PageRoutingPlan(
                page_id=page_id,
                regions=regions,
                fallback_applied=False,
                dominant_route=dominant,
            )

        # 2. Page-level document type / page_role hints
        doc_type = page_payload.get("doc_type")
        page_role = page_payload.get("page_role")

        if doc_type in ("cadastral_map", "fmb_sketch") or page_role == "map_sheet":
            map_region = LogicalRegion(
                region_id=f"{page_id}_map_full",
                route=LogicalRoute.NON_TEXT_MAP,
                bbox=BoundingBox(x=0.0, y=0.0, w=1000.0, h=1000.0),
                confidence=0.95,
                metadata={"doc_type": doc_type, "page_role": page_role},
            )
            return PageRoutingPlan(
                page_id=page_id,
                regions=[map_region],
                fallback_applied=False,
                dominant_route=LogicalRoute.NON_TEXT_MAP,
            )

        if page_role == "tabular_register":
            table_region = LogicalRegion(
                region_id=f"{page_id}_table_full",
                route=LogicalRoute.RULED_TABLE,
                bbox=BoundingBox(x=0.0, y=0.0, w=1000.0, h=1000.0),
                confidence=0.9,
                metadata={"doc_type": doc_type, "page_role": page_role},
            )
            return PageRoutingPlan(
                page_id=page_id,
                regions=[table_region],
                fallback_applied=False,
                dominant_route=LogicalRoute.RULED_TABLE,
            )

        if page_role == "endorsement":
            hwr_region = LogicalRegion(
                region_id=f"{page_id}_endorsement_hwr",
                route=LogicalRoute.HANDWRITTEN_TEXT,
                bbox=BoundingBox(x=0.0, y=0.0, w=1000.0, h=1000.0),
                confidence=0.85,
                metadata={"page_role": page_role},
            )
            return PageRoutingPlan(
                page_id=page_id,
                regions=[hwr_region],
                fallback_applied=False,
                dominant_route=LogicalRoute.HANDWRITTEN_TEXT,
            )

        # 3. Conservative Fallback: Default full-page printed text region
        logger.info("No layout regions or specific page role hints; using conservative printed_text fallback for page %s", page_id)
        fallback_region = LogicalRegion(
            region_id=f"{page_id}_printed_fallback",
            route=LogicalRoute.PRINTED_TEXT,
            bbox=BoundingBox(x=0.0, y=0.0, w=1000.0, h=1000.0),
            confidence=1.0,
            metadata={"fallback": True},
        )
        return PageRoutingPlan(
            page_id=page_id,
            regions=[fallback_region],
            fallback_applied=True,
            dominant_route=LogicalRoute.PRINTED_TEXT,
        )

    def _compute_dominant_route(self, regions: list[LogicalRegion]) -> LogicalRoute:
        if not regions:
            return LogicalRoute.PRINTED_TEXT
        route_counts: dict[LogicalRoute, int] = {}
        for r in regions:
            route_counts[r.route] = route_counts.get(r.route, 0) + 1
        sorted_routes = sorted(route_counts.items(), key=lambda x: x[1], reverse=True)
        return sorted_routes[0][0]


class RegionDispatcher:
    """Dispatches page regions to their configured route engine implementations."""

    def __init__(self, engine_map: dict[LogicalRoute, Any] | None = None):
        self.engine_map = engine_map or {}

    def dispatch_and_extract(
        self,
        page_payload: dict[str, Any],
        routing_plan: PageRoutingPlan,
        config_version: str = "v1",
    ) -> list[OCRResult]:
        """Dispatches regions to engine implementations and collects normalized OCRResults.

        - `NON_TEXT_MAP` regions BYPASS text OCR completely (not blindly sent through text OCR).
        - `HANDWRITTEN_TEXT` regions use HWR adapter route (without page-wide HWR assumption).
        - `RULED_TABLE` regions use table extractor route (or printed engine if specified).
        - `PRINTED_TEXT` regions use printed OCR engine adapter.
        """
        page_id = routing_plan.page_id
        image_bytes = page_payload.get("image_bytes") or b"fake_image_bytes"
        results: list[OCRResult] = []

        printed_engine: OCREngineAdapter = self.engine_map.get(LogicalRoute.PRINTED_TEXT) or BaiduUnlimitedOCRAdapter(allow_test_fallback=True)

        for region in routing_plan.regions:
            route = region.route

            # 1. Non-Text / Map Regions: BYPASS text OCR processing completely
            if route == LogicalRoute.NON_TEXT_MAP:
                logger.info("Bypassing text OCR for non-text/map region %s on page %s", region.region_id, page_id)
                map_result = OCRResult(
                    page_id=page_id,
                    tokens=[],  # Zero text tokens produced
                    full_text="",
                    engine_id="map_bypassed",
                    model_version="non-text-v1",
                    config_version=config_version,
                )
                results.append(map_result)

            # 2. Ruled Table Regions
            elif route == LogicalRoute.RULED_TABLE:
                tbl_adapter = self.engine_map.get(LogicalRoute.RULED_TABLE)
                if tbl_adapter and hasattr(tbl_adapter, "process_image"):
                    res = tbl_adapter.process_image(image_bytes=image_bytes, page_id=page_id, config_version=config_version)
                elif self.engine_map.get(LogicalRoute.PRINTED_TEXT) and hasattr(printed_engine, "process_image"):
                    res = printed_engine.process_image(image_bytes=image_bytes, page_id=page_id, config_version=config_version)
                else:
                    res = extract_table_cells(image_bytes=image_bytes, page_id=page_id, config_version=config_version)
                results.append(res)

            # 3. Handwritten Text Regions
            elif route == LogicalRoute.HANDWRITTEN_TEXT:
                hwr_engine: OCREngineAdapter = self.engine_map.get(LogicalRoute.HANDWRITTEN_TEXT) or HWRAdapter()
                res = hwr_engine.process_image(image_bytes=image_bytes, page_id=page_id, config_version=config_version)
                results.append(res)

            # 4. Printed Indic/English Text Regions (Default)
            else:
                res = printed_engine.process_image(image_bytes=image_bytes, page_id=page_id, config_version=config_version)
                results.append(res)

        return results
