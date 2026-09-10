"""Unit tests for survey/khasra label to parcel polygon binding (FR-MAP-04).

Tests required:
- one label / one parcel
- multiple parcels
- multiple labels
- ambiguous label
- missing label
"""
import pytest
from extraction.domain.georeference import GeoreferenceResult
from extraction.domain.vectorize import bind_survey_labels


@pytest.fixture
def identity_georef():
    return GeoreferenceResult(
        page_id="p-test",
        transform_matrix=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        transform_type="affine",
        rmse=0.1,
        crs="EPSG:32643",
        control_points_count=4,
    )


def test_bind_one_label_one_parcel(identity_georef):
    """1. One label / one parcel: preserves text, confidence, bbox, and lineage."""
    polygons = [
        {
            "boundary_id": "b1",
            "is_valid": True,
            "polygon": {"type": "Polygon", "coordinates": [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]]},
            "conflation_lineage_ref": "lineage-poly-1",
        }
    ]
    labels = [
        {
            "raw_value": "Khasra 101",
            "x": 50.0,
            "y": 50.0,
            "confidence": 0.98,
            "bbox": {"x": 45.0, "y": 45.0, "w": 10.0, "h": 10.0},
            "conflation_lineage_ref": "lineage-lbl-101",
        }
    ]

    bound = bind_survey_labels(polygons, labels, georef_result=identity_georef)
    assert len(bound) == 1
    item = bound[0]

    assert item["bound_survey_no"] == "Khasra 101"
    assert item["label_confidence"] == 0.98
    assert item["label_bbox"] == {"x": 45.0, "y": 45.0, "w": 10.0, "h": 10.0}
    assert item["binding_status"] == "bound"
    assert item["conflation_lineage_ref"] == "lineage-lbl-101"


def test_bind_multiple_parcels(identity_georef):
    """2. Multiple parcels: each parcel correctly binds to its respective label."""
    polygons = [
        {"boundary_id": "p1", "is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]]}},
        {"boundary_id": "p2", "is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(100, 0), (200, 0), (200, 100), (100, 100), (100, 0)]]}},
    ]
    labels = [
        {"raw_value": "Survey 12A", "x": 50.0, "y": 50.0, "confidence": 0.92},
        {"raw_value": "Survey 12B", "x": 150.0, "y": 50.0, "confidence": 0.95},
    ]

    bound = bind_survey_labels(polygons, labels, georef_result=identity_georef)
    assert len(bound) == 2
    assert bound[0]["bound_survey_no"] == "Survey 12A"
    assert bound[0]["binding_status"] == "bound"
    assert bound[1]["bound_survey_no"] == "Survey 12B"
    assert bound[1]["binding_status"] == "bound"


def test_bind_multiple_labels_in_same_parcel(identity_georef):
    """3. Multiple labels: multiple competing distinct labels inside a single parcel trigger ambiguous handling."""
    polygons = [
        {"boundary_id": "p-shared", "is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]]}}
    ]
    labels = [
        {"raw_value": "Khasra 45/1", "x": 30.0, "y": 30.0, "confidence": 0.90},
        {"raw_value": "Khasra 45/2", "x": 70.0, "y": 70.0, "confidence": 0.88},
    ]

    bound = bind_survey_labels(polygons, labels, georef_result=identity_georef)
    assert len(bound) == 1
    item = bound[0]

    # Competing labels in single parcel must NOT force arbitrary binding
    assert item["bound_survey_no"] is None
    assert item["binding_status"] == "ambiguous"


def test_bind_ambiguous_label_overlapping_parcels(identity_georef):
    """4. Ambiguous label: a label lying inside multiple overlapping parcels does not force binding."""
    # Two overlapping parcels share region (50,0) to (100,100)
    polygons = [
        {"boundary_id": "p-left", "is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]]}},
        {"boundary_id": "p-overlap", "is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(50, 0), (150, 0), (150, 100), (50, 100), (50, 0)]]}},
    ]
    labels = [
        # Label located inside overlapping region x=75, y=50
        {"raw_value": "Ambiguous 99", "x": 75.0, "y": 50.0, "confidence": 0.91}
    ]

    bound = bind_survey_labels(polygons, labels, georef_result=identity_georef)
    assert len(bound) == 2

    # Both parcels see an ambiguous label contained in both geometries
    assert bound[0]["bound_survey_no"] is None
    assert bound[0]["binding_status"] == "ambiguous"
    assert bound[1]["bound_survey_no"] is None
    assert bound[1]["binding_status"] == "ambiguous"


def test_bind_missing_label(identity_georef):
    """5. Missing label: parcel with no label present returns bound_survey_no as None and binding_status as unbound."""
    polygons = [
        {"boundary_id": "p-nolabel", "is_valid": True, "polygon": {"type": "Polygon", "coordinates": [[(0, 0), (50, 0), (50, 50), (0, 50), (0, 0)]]}}
    ]
    labels = [
        # Label far away at (500, 500)
        {"raw_value": "Distant 999", "x": 500.0, "y": 500.0, "confidence": 0.85}
    ]

    bound = bind_survey_labels(polygons, labels, georef_result=identity_georef)
    assert len(bound) == 1
    item = bound[0]

    assert item["bound_survey_no"] is None
    assert item["label_confidence"] is None
    assert item["label_bbox"] is None
    assert item["binding_status"] == "unbound"
