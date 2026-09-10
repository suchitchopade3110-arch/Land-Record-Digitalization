"""End-to-end integration tests for M3 OCR and M4 GIS extraction pipelines.

Tests:
1. text-only document
2. map-only document
3. mixed document (text + map)
4. multi-page record assembly
5. OCR failure isolation
6. GIS failure isolation
7. malformed OCR result safe handling
8. ambiguous extraction handling
"""
import pytest
from extraction.workers import map_lane, text_lane, triage_consumer


def test_text_only_document_integration():
    """1. Text-only document: triage routes to text_lane; map_lane skipped."""
    message = {
        "trace_id": "doc-text-01:page-1",
        "work_envelope": {"document_id": "doc-text-01", "stage": "triage"},
        "payload": {
            "document_id": "doc-text-01",
            "page_id": "page-text-1",
            "route": ["text"],
            "page_role": "text",
            "ocr_words": [
                {"text": "Owner: Ramesh Sharma", "location": {"top": 10, "left": 10, "width": 200, "height": 20}},
                {"text": "Share: 1/2", "location": {"top": 40, "left": 10, "width": 100, "height": 20}},
                {"text": "Survey Number: 45/1", "location": {"top": 70, "left": 10, "width": 150, "height": 20}},
            ],
        },
    }

    results = triage_consumer.handle(message)
    assert "text_lane" in results
    assert "map_lane" not in results

    tl_res = results["text_lane"]
    assert tl_res["status"] == "success"
    assert tl_res["extractions_count"] >= 1
    assert "relationships" in tl_res

    text_env = tl_res["text_lane_envelope"]
    assert text_env["_queue"] == "TEXT_QUEUE"
    assert text_env["producer"] == "text-lane"

    assembly_env = tl_res["assembly_envelope"]
    assert assembly_env["_queue"] == "ASSEMBLY_QUEUE"
    assert assembly_env["producer"] == "assembly"


def test_map_only_document_integration():
    """2. Map-only document: triage routes to map_lane; text_lane skipped."""
    message = {
        "trace_id": "doc-map-01:page-1",
        "work_envelope": {"document_id": "doc-map-01", "stage": "triage"},
        "payload": {
            "document_id": "doc-map-01",
            "page_id": "page-map-1",
            "doc_type": "cadastral_map",
            "page_role": "map_sheet",
            "route": ["map"],
            "control_points": [
                {"pixel": [0, 0], "map": [500000.0, 3000000.0]},
                {"pixel": [100, 0], "map": [500100.0, 3000000.0]},
                {"pixel": [100, 100], "map": [500100.0, 3000100.0]},
                {"pixel": [0, 100], "map": [500000.0, 3000100.0]},
            ],
            "raw_polygons": [
                {"coordinates": [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]}
            ],
            "labels": [{"raw_value": "Khasra 101", "x": 50.0, "y": 50.0}],
            "conflation_lineage_ref": "00000000-0000-0000-0000-000000000001",
        },
    }

    results = triage_consumer.handle(message)
    assert "map_lane" in results
    assert "text_lane" not in results

    ml_res = results["map_lane"]
    assert ml_res["status"] == "success"
    assert ml_res["parcel_geometries_count"] == 1

    map_env = ml_res["map_lane_envelope"]
    assert map_env["_queue"] == "MAP_QUEUE"
    assert map_env["producer"] == "map-lane"

    pg = map_env["payload"][0]
    assert pg["bound_survey_no"] == "Khasra 101"
    assert pg["area_computed"] == "10000.00"
    assert pg["ulpin_eligible"] is True


def test_mixed_document_integration():
    """3. Mixed document: triage routes to BOTH text_lane and map_lane."""
    message = {
        "trace_id": "doc-mixed-01:page-1",
        "work_envelope": {"document_id": "doc-mixed-01", "stage": "triage"},
        "payload": {
            "document_id": "doc-mixed-01",
            "page_id": "page-mixed-1",
            "route": ["text", "map"],
            "ocr_words": [
                {"text": "Owner: Suresh Patel", "location": {"top": 10, "left": 10, "width": 200, "height": 20}}
            ],
            "control_points": [
                {"pixel": [0, 0], "map": [500000.0, 3000000.0]},
                {"pixel": [100, 0], "map": [500100.0, 3000000.0]},
                {"pixel": [100, 100], "map": [500100.0, 3000100.0]},
                {"pixel": [0, 100], "map": [500000.0, 3000100.0]},
            ],
            "raw_polygons": [
                {"coordinates": [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]}
            ],
        },
    }

    results = triage_consumer.handle(message)
    assert "text_lane" in results
    assert "map_lane" in results

    assert results["text_lane"]["status"] == "success"
    assert results["map_lane"]["status"] == "success"


def test_multi_page_record_assembly_integration():
    """4. Multi-page record assembly preserves page provenance across pages."""
    msg1 = {
        "trace_id": "doc-mp-01:page-1",
        "work_envelope": {"document_id": "doc-mp-01"},
        "payload": {
            "page_id": "p-1",
            "route": ["text"],
            "ocr_words": [
                {"text": "Owner: Anita Roy", "location": {"top": 10, "left": 10, "width": 150, "height": 20}}
            ],
        },
    }
    msg2 = {
        "trace_id": "doc-mp-01:page-2",
        "work_envelope": {"document_id": "doc-mp-01"},
        "payload": {
            "page_id": "p-2",
            "route": ["text"],
            "ocr_words": [
                {"text": "Survey Number: 99", "location": {"top": 10, "left": 10, "width": 150, "height": 20}}
            ],
        },
    }

    res1 = triage_consumer.handle(msg1)
    res2 = triage_consumer.handle(msg2)

    assert res1["text_lane"]["status"] == "success"
    assert res2["text_lane"]["status"] == "success"

    assembly1 = res1["text_lane"]["assembly_envelope"]["payload"]["record_assembly"]
    assembly2 = res2["text_lane"]["assembly_envelope"]["payload"]["record_assembly"]

    assert len(assembly1["extraction_ids"]) >= 1
    assert len(assembly2["extraction_ids"]) >= 1


def test_ocr_failure_isolation():
    """5. OCR failure in text lane does not corrupt map processing or crash triage."""
    message = {
        "trace_id": "doc-err-01:page-1",
        "work_envelope": {"document_id": "doc-err-01"},
        "payload": {
            "page_id": "p-err",
            "route": ["text", "map"],
            "image_bytes": None,  # Missing image bytes safely returns 0 extractions
            "control_points": [
                {"pixel": [0, 0], "map": [500000.0, 3000000.0]},
                {"pixel": [100, 0], "map": [500100.0, 3000000.0]},
                {"pixel": [100, 100], "map": [500100.0, 3000100.0]},
                {"pixel": [0, 100], "map": [500000.0, 3000100.0]},
            ],
            "raw_polygons": [
                {"coordinates": [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]}
            ],
        },
    }

    results = triage_consumer.handle(message)
    assert "text_lane" in results
    assert "map_lane" in results

    # Text lane handles missing OCR safely with 0 extractions without crashing
    assert results["text_lane"]["status"] == "success"
    assert results["text_lane"]["extractions_count"] == 0

    # Map lane succeeded independently
    assert results["map_lane"]["status"] == "success"
    assert results["map_lane"]["parcel_geometries_count"] == 1


def test_gis_failure_isolation():
    """6. GIS failure (missing control points or empty map payload) does not corrupt text processing."""
    message = {
        "trace_id": "doc-gis-err-01:page-1",
        "work_envelope": {"document_id": "doc-gis-err-01"},
        "payload": {
            "page_id": "p-gis-err",
            "route": ["text", "map"],
            "ocr_words": [
                {"text": "Owner: Vikram Singh", "location": {"top": 10, "left": 10, "width": 150, "height": 20}}
            ],
            "allow_default_parcels": False,
            "raw_polygons": [],  # No polygons -> 0 parcel geometries
        },
    }

    results = triage_consumer.handle(message)
    assert "text_lane" in results
    assert "map_lane" in results

    # Text lane succeeded
    assert results["text_lane"]["status"] == "success"
    assert results["text_lane"]["extractions_count"] >= 1

    # Map lane safely completed with 0 parcel geometries without crashing
    assert results["map_lane"]["status"] == "success"
    assert results["map_lane"]["parcel_geometries_count"] == 0


def test_malformed_ocr_result_handling():
    """7. Malformed OCR result handling."""
    msg = {
        "trace_id": "doc-mal-01",
        "payload": {
            "page_id": "p-mal",
            "route": ["text"],
            "image_bytes": None,
        },
    }
    res = text_lane.handle(msg)
    assert res["status"] == "success"
    assert res["extractions_count"] == 0


def test_ambiguous_extraction_handling():
    """8. Ambiguous extraction handling (label inside overlapping parcel geometries)."""
    msg = {
        "trace_id": "doc-amb-01",
        "payload": {
            "page_id": "p-amb",
            "route": ["map"],
            "control_points": [
                {"pixel": [0, 0], "map": [0.0, 0.0]},
                {"pixel": [100, 0], "map": [100.0, 0.0]},
                {"pixel": [100, 100], "map": [100.0, 100.0]},
                {"pixel": [0, 100], "map": [0.0, 100.0]},
            ],
            "raw_polygons": [
                {"boundary_id": "b1", "coordinates": [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]},
                {"boundary_id": "b2", "coordinates": [(50, 0), (150, 0), (150, 100), (50, 100), (50, 0)]},
            ],
            "labels": [{"raw_value": "Ambiguous Label", "x": 75.0, "y": 50.0}],
        },
    }

    res = map_lane.handle(msg)
    assert res["status"] == "success"
    geoms = res["map_lane_envelope"]["payload"]
    assert len(geoms) == 2
    # Both geometries reject forced ambiguous binding
    assert geoms[0]["bound_survey_no"] is None
    assert geoms[1]["bound_survey_no"] is None
