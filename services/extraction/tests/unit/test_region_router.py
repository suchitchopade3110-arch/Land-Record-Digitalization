"""Unit tests for M3 P0 Region Router & Dispatcher (FR-OCR-04 / FR-TRI-05).

Tests:
1. Route printed text region (printed_text)
2. Route handwritten text region (handwritten_text)
3. Route ruled table region (ruled_table)
4. Route non-text / map region (non_text_map - bypasses text OCR)
5. Conservative fallback behavior (defaults to printed_text when layout hints absent)
6. Region bounding box preservation
7. Dispatching different engines per region route
"""
import pytest
from extraction.domain.ocr.hwr_adapter import HWRAdapter
from extraction.domain.ocr.interfaces import BoundingBox
from extraction.domain.ocr.region_router import (
    LogicalRegion,
    LogicalRoute,
    PageRoutingPlan,
    RegionDispatcher,
    RegionRouter,
)


def test_router_explicit_layout_regions_classification():
    router = RegionRouter()
    payload = {
        "page_id": "page-layout-001",
        "layout_regions": [
            {
                "id": "r1",
                "type": "printed_text",
                "bbox": {"x": 10.0, "y": 10.0, "w": 500.0, "h": 100.0},
            },
            {
                "id": "r2",
                "type": "handwritten",
                "is_handwritten": True,
                "bbox": {"x": 10.0, "y": 120.0, "w": 500.0, "h": 80.0},
            },
            {
                "id": "r3",
                "type": "table",
                "is_table": True,
                "bbox": {"x": 10.0, "y": 210.0, "w": 500.0, "h": 200.0},
            },
            {
                "id": "r4",
                "type": "cadastral_map",
                "is_map": True,
                "bbox": {"x": 10.0, "y": 420.0, "w": 500.0, "h": 300.0},
            },
        ],
    }

    plan = router.route_page(payload)
    assert plan.page_id == "page-layout-001"
    assert plan.fallback_applied is False
    assert len(plan.regions) == 4

    r1, r2, r3, r4 = plan.regions

    # 1. Printed Text
    assert r1.route == LogicalRoute.PRINTED_TEXT
    assert r1.bbox == BoundingBox(x=10.0, y=10.0, w=500.0, h=100.0)

    # 2. Handwritten Text
    assert r2.route == LogicalRoute.HANDWRITTEN_TEXT
    assert r2.bbox == BoundingBox(x=10.0, y=120.0, w=500.0, h=80.0)

    # 3. Ruled Table
    assert r3.route == LogicalRoute.RULED_TABLE
    assert r3.bbox == BoundingBox(x=10.0, y=210.0, w=500.0, h=200.0)

    # 4. Non-text / Map
    assert r4.route == LogicalRoute.NON_TEXT_MAP
    assert r4.bbox == BoundingBox(x=10.0, y=420.0, w=500.0, h=300.0)


def test_router_conservative_fallback():
    """When no layout regions or page role hints exist, fallback conservatively to printed_text."""
    router = RegionRouter()
    payload = {"page_id": "page-fallback-002"}

    plan = router.route_page(payload)
    assert plan.page_id == "page-fallback-002"
    assert plan.fallback_applied is True
    assert plan.dominant_route == LogicalRoute.PRINTED_TEXT
    assert len(plan.regions) == 1
    assert plan.regions[0].route == LogicalRoute.PRINTED_TEXT


def test_router_page_role_hints():
    router = RegionRouter()

    # Map sheet
    map_plan = router.route_page({"page_id": "p-map", "doc_type": "cadastral_map"})
    assert map_plan.dominant_route == LogicalRoute.NON_TEXT_MAP

    # Tabular register
    table_plan = router.route_page({"page_id": "p-tbl", "page_role": "tabular_register"})
    assert table_plan.dominant_route == LogicalRoute.RULED_TABLE

    # Endorsement (Handwritten)
    hw_plan = router.route_page({"page_id": "p-hw", "page_role": "endorsement"})
    assert hw_plan.dominant_route == LogicalRoute.HANDWRITTEN_TEXT


def test_dispatcher_bypasses_non_text_map_from_ocr():
    """Non-text/map regions must NOT be blindly sent through text OCR."""
    dispatcher = RegionDispatcher()
    payload = {"page_id": "page-map-003", "image_bytes": b"fake_map_image"}
    plan = PageRoutingPlan(
        page_id="page-map-003",
        regions=[
            LogicalRegion(
                region_id="r-map",
                route=LogicalRoute.NON_TEXT_MAP,
                bbox=BoundingBox(x=0.0, y=0.0, w=100.0, h=100.0),
            )
        ],
    )

    results = dispatcher.dispatch_and_extract(payload, plan)
    assert len(results) == 1
    res = results[0]
    assert res.engine_id == "map_bypassed"
    assert res.tokens == []
    assert res.full_text == ""


def test_dispatcher_routes_handwritten_and_table_regions():
    dispatcher = RegionDispatcher()
    payload = {"page_id": "page-multi-004", "image_bytes": b"fake_multi_image"}
    plan = PageRoutingPlan(
        page_id="page-multi-004",
        regions=[
            LogicalRegion(
                region_id="r-hw",
                route=LogicalRoute.HANDWRITTEN_TEXT,
                bbox=BoundingBox(x=0.0, y=0.0, w=100.0, h=50.0),
            ),
            LogicalRegion(
                region_id="r-tbl",
                route=LogicalRoute.RULED_TABLE,
                bbox=BoundingBox(x=0.0, y=50.0, w=100.0, h=50.0),
            ),
        ],
    )

    results = dispatcher.dispatch_and_extract(payload, plan)
    assert len(results) == 2

    hw_res = results[0]
    assert hw_res.engine_id == "hwr"
    assert len(hw_res.tokens) > 0

    tbl_res = results[1]
    assert tbl_res.engine_id == "table_extractor"
    assert len(tbl_res.tokens) > 0
