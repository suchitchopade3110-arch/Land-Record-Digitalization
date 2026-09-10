"""Unit tests for M4 Georeferencing (FR-MAP-01, FR-MAP-09).

Tests:
1. Preserve CRS and source page identity
2. Preserve control points and record transform type
3. Calculate georeferencing RMSE accurately
4. Reject insufficient control points (<3 GCPs) safely without fabricating coordinates
5. Reject collinear / degenerate control points safely
"""
import pytest
from extraction.domain.georeference import (
    GCP,
    GeoreferenceResult,
    Georeferencer,
    InsufficientControlPointsError,
    georeference,
)


def test_georeference_preserve_crs_page_id_and_transform_type():
    georeferencer = Georeferencer()
    page_id = "page-geo-001"
    crs = "EPSG:4326"

    gcps = [
        GCP(pixel_x=0.0, pixel_y=0.0, geo_x=77.100, geo_y=28.600),
        GCP(pixel_x=100.0, pixel_y=0.0, geo_x=77.200, geo_y=28.600),
        GCP(pixel_x=100.0, pixel_y=100.0, geo_x=77.200, geo_y=28.700),
        GCP(pixel_x=0.0, pixel_y=100.0, geo_x=77.100, geo_y=28.700),
    ]

    payload = {
        "page_id": page_id,
        "gcp_control_points": gcps,
        "crs": crs,
    }

    res = georeferencer.georeference_map(payload)

    assert isinstance(res, GeoreferenceResult)
    assert res.page_id == page_id
    assert res.crs == crs
    assert res.transform_type == "affine"
    assert res.control_points_count == 4
    assert len(res.gcps) == 4
    assert res.rmse == pytest.approx(0.0, abs=1e-4)


def test_georeference_rmse_calculation():
    georeferencer = Georeferencer()

    gcps = [
        GCP(pixel_x=0.0, pixel_y=0.0, geo_x=0.0, geo_y=0.0),
        GCP(pixel_x=100.0, pixel_y=0.0, geo_x=100.0, geo_y=0.0),
        GCP(pixel_x=0.0, pixel_y=100.0, geo_x=0.0, geo_y=100.0),
        GCP(pixel_x=100.0, pixel_y=100.0, geo_x=101.5, geo_y=101.5),  # 2.12m residual
    ]

    res = georeferencer.georeference_map({"page_id": "p-rmse", "gcp_control_points": gcps})
    assert res.rmse > 0.0
    assert res.rmse == pytest.approx(0.53, abs=0.05)


def test_reject_insufficient_control_points_safely():
    georeferencer = Georeferencer()

    # Only 2 GCPs provided with allow_fallback=False
    insufficient_gcps = [
        GCP(pixel_x=0.0, pixel_y=0.0, geo_x=0.0, geo_y=0.0),
        GCP(pixel_x=100.0, pixel_y=0.0, geo_x=100.0, geo_y=0.0),
    ]

    payload = {"page_id": "p-fail-1", "gcp_control_points": insufficient_gcps}

    with pytest.raises(InsufficientControlPointsError) as exc_info:
        georeferencer.georeference_map(payload, allow_fallback=False)

    assert "requires at least 3 non-collinear Ground Control Points" in str(exc_info.value)
    assert "p-fail-1" in str(exc_info.value)


def test_reject_collinear_control_points_safely():
    georeferencer = Georeferencer()

    # 3 collinear GCPs (all along y=0)
    collinear_gcps = [
        GCP(pixel_x=0.0, pixel_y=0.0, geo_x=0.0, geo_y=0.0),
        GCP(pixel_x=50.0, pixel_y=0.0, geo_x=50.0, geo_y=0.0),
        GCP(pixel_x=100.0, pixel_y=0.0, geo_x=100.0, geo_y=0.0),
    ]

    payload = {"page_id": "p-collinear", "gcp_control_points": collinear_gcps}

    with pytest.raises(InsufficientControlPointsError) as exc_info:
        georeferencer.georeference_map(payload, allow_fallback=True)

    assert "collinear or degenerate" in str(exc_info.value)


def test_georeference_backwards_compatible_dict():
    res_dict = georeference({"page_id": "p-compat"})
    assert res_dict["page_id"] == "p-compat"
    assert "transform_matrix" in res_dict
    assert "rmse" in res_dict
    assert "crs" in res_dict
