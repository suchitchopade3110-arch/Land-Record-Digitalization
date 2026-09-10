"""Unit & contract tests for M4 map lane worker & pipeline (FR-MAP-01..12)."""
import json
import jsonschema
import pytest
from extraction.domain.georeference import GCP
from extraction.domain.parcel_pipeline import process_map_page
from extraction.workers.map_lane import handle


def test_process_map_page_parcel_geometry_contract_schema(tmp_path):
    # Load frozen ParcelGeometry contract schema
    schema_path = "contracts/schemas/parcel_geometry.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    gcps = [
        GCP(pixel_x=0.0, pixel_y=0.0, geo_x=500000.0, geo_y=3000000.0),
        GCP(pixel_x=1000.0, pixel_y=0.0, geo_x=501000.0, geo_y=3000000.0),
        GCP(pixel_x=1000.0, pixel_y=1000.0, geo_x=501000.0, geo_y=3001000.0),
        GCP(pixel_x=0.0, pixel_y=1000.0, geo_x=500000.0, geo_y=3001000.0),
    ]

    payload = {
        "page_id": "00000000-0000-0000-0000-000000000001",
        "document_id": "00000000-0000-0000-0000-000000000002",
        "gcp_control_points": gcps,
        "crs": "EPSG:32643",
        "labels": [{"raw_value": "Khasra 100", "geo_x": 500200.0, "geo_y": 3000200.0}],
    }

    geometries = process_map_page(payload)
    assert len(geometries) > 0

    for pg in geometries:
        # Validate against JSON Schema (additionalProperties: false)
        jsonschema.validate(instance=pg, schema=schema)
        assert isinstance(pg["area_computed"], str)
        assert pg["ulpin_eligible"] is True  # RMSE is 0.0 <= 1.5m threshold


def test_map_lane_worker_handle():
    message = {
        "trace_id": "doc-map-1:page-map-1",
        "work_envelope": {"config_version": "v1"},
        "payload": {
            "page_id": "00000000-0000-0000-0000-000000000010",
            "crs": "EPSG:32643",
        },
    }

    res = handle(message)
    assert res["status"] == "success"
    assert res["parcel_geometries_count"] > 0
    assert "map_lane_envelope" in res
    assert res["map_lane_envelope"]["producer"] == "map-lane"
    assert "payload" in res["map_lane_envelope"]
