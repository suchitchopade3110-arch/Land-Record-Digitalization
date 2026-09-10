"""Unit tests for P0 Parcel Boundary Vectorization (FR-MAP-02/03/04).

Tests:
1. Simple parcel
2. Multiple parcels
3. Closed boundary validation
4. Broken boundary handling (conservative rejection without inventing missing edges)
5. Noisy boundary handling (deduplicating coincident vertices)
6. Invalid polygon handling (self-intersecting bowtie detection)
"""
import pytest
from extraction.domain.georeference import GCP, GeoreferenceResult
from extraction.domain.vectorize import (
    bind_survey_labels,
    compute_area,
    validate_polygon_geometry,
    vectorize_parcels,
)


def test_simple_parcel_vectorization():
    """1. Simple 4-vertex parcel."""
    georef = GeoreferenceResult(
        page_id="p-1",
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

    item = parcels[0]
    assert item["is_valid"] is True
    assert item["is_broken"] is False
    assert item["polygon"]["type"] == "Polygon"

    coords = item["polygon"]["coordinates"][0]
    assert len(coords) == 5
    assert coords[0] == [500000.0, 3000000.0]
    assert coords[2] == [500100.0, 3000100.0]


def test_multiple_parcels_vectorization():
    """2. Multiple parcels in map sheet."""
    georef = GeoreferenceResult(
        page_id="p-multi",
        transform_matrix=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        transform_type="affine",
        rmse=0.1,
        crs="EPSG:32643",
        control_points_count=4,
    )

    payload = {
        "conflation_lineage_ref": "lineage-uuid-123",
        "raw_polygons": [
            {"boundary_id": "b1", "coordinates": [(0, 0), (50, 0), (50, 50), (0, 50), (0, 0)]},
            {"boundary_id": "b2", "coordinates": [(50, 0), (100, 0), (100, 50), (50, 50), (50, 0)]},
        ],
    }

    parcels = vectorize_parcels(payload, georef_result=georef)
    assert len(parcels) == 2
    assert parcels[0]["boundary_id"] == "b1"
    assert parcels[1]["boundary_id"] == "b2"
    assert parcels[0]["conflation_lineage_ref"] == "lineage-uuid-123"


def test_closed_boundary_validation():
    """3. Closed boundary ring validation."""
    closed_coords = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
    is_valid, reason = validate_polygon_geometry(closed_coords)
    assert is_valid is True
    assert "Valid" in reason


def test_broken_boundary_handling():
    """4. Broken/unclosed boundary handling without inventing missing edges."""
    georef = GeoreferenceResult(
        page_id="p-broken",
        transform_matrix=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        transform_type="affine",
        rmse=0.1,
        crs="EPSG:32643",
        control_points_count=4,
    )

    payload = {
        "raw_polygons": [
            {
                "boundary_id": "b-open",
                "is_closed": False,
                "coordinates": [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],  # Open line segment
            }
        ]
    }

    parcels = vectorize_parcels(payload, georef_result=georef)
    assert len(parcels) == 1
    item = parcels[0]

    assert item["is_valid"] is False
    assert item["is_broken"] is True
    assert item["polygon"] is None
    assert "Broken or open boundary" in item["validation_reason"]


def test_noisy_boundary_deduplication():
    """5. Noisy boundary with duplicate coincident vertices."""
    georef = GeoreferenceResult(
        page_id="p-noisy",
        transform_matrix=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        transform_type="affine",
        rmse=0.1,
        crs="EPSG:32643",
        control_points_count=4,
    )

    # Coincident noise at (0,0) and (10,0)
    noisy_coords = [(0, 0), (0, 0), (10, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    payload = {"raw_polygons": [{"coordinates": noisy_coords}]}

    parcels = vectorize_parcels(payload, georef_result=georef)
    assert len(parcels) == 1
    item = parcels[0]

    assert item["is_valid"] is True
    coords = item["polygon"]["coordinates"][0]
    assert len(coords) == 5  # Duplicate noise stripped cleanly


def test_invalid_polygon_self_intersection():
    """6. Invalid self-intersecting (bowtie) polygon ring."""
    # Bowtie crossing: (0,0) -> (10,10) -> (0,10) -> (10,0) -> (0,0)
    bowtie_coords = [[0.0, 0.0], [10.0, 10.0], [0.0, 10.0], [10.0, 0.0], [0.0, 0.0]]

    is_valid, reason = validate_polygon_geometry(bowtie_coords)
    assert is_valid is False
    assert "self-intersecting" in reason.lower() or "invalid" in reason.lower()


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
        {"is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]]}},
        {"is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(100, 0), (200, 0), (200, 100), (100, 100), (100, 0)]]}},
    ]

    labels = [
        {"raw_value": "Khasra 45/1", "x": 50.0, "y": 50.0},
        {"raw_value": "Khasra 45/2", "x": 150.0, "y": 50.0},
    ]

    bound = bind_survey_labels(polygons, labels, georef_result=georef)
    assert len(bound) == 2
    assert bound[0]["bound_survey_no"] == "Khasra 45/1"
    assert bound[1]["bound_survey_no"] == "Khasra 45/2"


def test_compute_area_exact_decimal_string():
    poly = {
        "type": "Polygon",
        "coordinates": [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0), (0.0, 0.0)]],
    }

    area_str = compute_area(poly)
    assert isinstance(area_str, str)
    assert area_str == "10000.00"
