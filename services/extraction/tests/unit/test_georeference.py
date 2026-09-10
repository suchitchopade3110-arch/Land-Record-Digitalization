"""Unit tests for M4 Georeferencing (FR-MAP-01, FR-MAP-09).

Tests:
1. Least-squares 2D affine transformation estimation
2. GCP RMSE residual calculation
3. Coordinate transformation mapping from pixel to geographic coordinates
4. Fallback GCP handling when explicit control points are missing
"""
import pytest
from extraction.domain.georeference import GCP, GeoreferenceResult, Georeferencer, georeference


def test_georeferencer_affine_transform_exact():
    georeferencer = Georeferencer()

    # Exact affine mapping: geo_x = 2 * px + 500000, geo_y = 3 * py + 3000000
    gcps = [
        GCP(pixel_x=0.0, pixel_y=0.0, geo_x=500000.0, geo_y=3000000.0),
        GCP(pixel_x=100.0, pixel_y=0.0, geo_x=500200.0, geo_y=3000000.0),
        GCP(pixel_x=0.0, pixel_y=100.0, geo_x=500000.0, geo_y=3000300.0),
        GCP(pixel_x=100.0, pixel_y=100.0, geo_x=500200.0, geo_y=3000300.0),
    ]

    payload = {"gcp_control_points": gcps, "crs": "EPSG:32643"}
    res = georeferencer.georeference_map(payload)

    assert isinstance(res, GeoreferenceResult)
    assert res.crs == "EPSG:32643"
    assert res.transform_type == "affine"
    assert res.control_points_count == 4
    assert res.rmse == pytest.approx(0.0, abs=1e-3)

    # Test point transformation
    gx, gy = res.transform_point(50.0, 50.0)
    assert gx == pytest.approx(500100.0, abs=1e-2)
    assert gy == pytest.approx(3000150.0, abs=1e-2)


def test_georeferencer_rmse_calculation():
    georeferencer = Georeferencer()

    # GCPs with known residuals (1 meter offset on GCP #4)
    gcps = [
        GCP(pixel_x=0.0, pixel_y=0.0, geo_x=0.0, geo_y=0.0),
        GCP(pixel_x=100.0, pixel_y=0.0, geo_x=100.0, geo_y=0.0),
        GCP(pixel_x=0.0, pixel_y=100.0, geo_x=0.0, geo_y=100.0),
        GCP(pixel_x=100.0, pixel_y=100.0, geo_x=101.0, geo_y=101.0),  # 1.414m residual
    ]

    res = georeferencer.georeference_map({"gcp_control_points": gcps})
    assert res.rmse > 0.0
    assert res.control_points_count == 4


def test_georeference_backwards_compatible_dict():
    res_dict = georeference({})
    assert "transform_matrix" in res_dict
    assert "rmse" in res_dict
    assert "crs" in res_dict
