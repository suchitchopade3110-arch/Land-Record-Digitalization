"""Unit tests for M4 Parcel Vectorization, Label Binding, and Area Calculation (FR-MAP-02/03/04)."""
import pytest
from extraction.domain.georeference import GCP, GeoreferenceResult
from extraction.domain.vectorize import bind_survey_labels, compute_area, vectorize_parcels


def test_vectorize_parcels_geojson_polygon():
    georef = GeoreferenceResult(
        page_id="page-1",
        transform_matrix=[1.0, 0.0, 500000.0, 0.0, 1.0, 3000000.0],
        transform_type="affine",
        rmse=0.5,
        crs="EPSG:32643",
        control_points_count=4,
    )

    payload = {
        "raw_polygons": [
            {"coordinates": [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]}
        ]
    }

    parcels = vectorize_parcels(payload, georef_result=georef)
    assert len(parcels) == 1

    poly = parcels[0]["polygon"]
    assert poly["type"] == "Polygon"

    coords = poly["coordinates"][0]
    assert len(coords) == 5  # Closed ring
    assert coords[0] == [500000.0, 3000000.0]
    assert coords[2] == [500100.0, 3000100.0]


def test_bind_survey_labels_point_in_polygon():
    georef = GeoreferenceResult(
        page_id="page-1",
        transform_matrix=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        transform_type="affine",
        rmse=0.2,
        crs="EPSG:32643",
        control_points_count=4,
    )

    polygons = [
        # Parcel 1: (0,0) to (100,100)
        {"polygon": {"type": "Polygon", "coordinates": [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]]}},
        # Parcel 2: (100,0) to (200,100)
        {"polygon": {"type": "Polygon", "coordinates": [[(100, 0), (200, 0), (200, 100), (100, 100), (100, 0)]]}},
    ]

    labels = [
        {"raw_value": "Khasra 45/1", "x": 50.0, "y": 50.0},  # Inside Parcel 1
        {"raw_value": "Khasra 45/2", "x": 150.0, "y": 50.0},  # Inside Parcel 2
    ]

    bound = bind_survey_labels(polygons, labels, georef_result=georef)
    assert len(bound) == 2
    assert bound[0]["bound_survey_no"] == "Khasra 45/1"
    assert bound[1]["bound_survey_no"] == "Khasra 45/2"


def test_compute_area_exact_decimal_string():
    # 100m x 100m polygon = 10000.00 sq m
    poly = {
        "type": "Polygon",
        "coordinates": [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]],
    }

    area_str = compute_area(poly)
    assert isinstance(area_str, str)
    assert area_str == "10000.00"  # Exact decimal string as per API-Contracts §1
