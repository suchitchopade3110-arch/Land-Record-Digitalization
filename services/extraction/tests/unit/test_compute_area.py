"""Unit tests for P0 polygon area computation with CRS sensitivity (FR-MAP-03/04).

Tests:
1. Projected CRS (e.g. EPSG:32643 UTM) known square area computation.
2. Geographic CRS (e.g. EPSG:4326 WGS84) lon/lat degree transformation to physical metric area.
3. CRS-sensitive calculation comparison.
4. Invalid/degenerate polygon safe handling ("0.00").
5. Exact decimal string precision ("10000.00").
"""
import pytest
from extraction.domain.vectorize import compute_area, is_geographic_crs


def test_projected_crs_known_square_area():
    """1. Projected CRS (EPSG:32643 UTM) 100m x 100m square -> exact 10000.00 m²."""
    poly = {
        "type": "Polygon",
        "coordinates": [[(500000.0, 3000000.0), (500100.0, 3000000.0), (500100.0, 3000100.0), (500000.0, 3000100.0), (500000.0, 3000000.0)]],
    }

    area_str = compute_area(poly, crs="EPSG:32643")
    assert isinstance(area_str, str)
    assert area_str == "10000.00"


def test_geographic_crs_lat_lon_transformation():
    """2. Geographic CRS (EPSG:4326 WGS84) transforms lat/lon degrees to physical area in m²."""
    # 0.001 deg lon x 0.001 deg lat near (77.0° E, 28.0° N)
    # Expected physical area is approx 10,941 m² (never 0.000001 deg²)
    poly_geo = {
        "type": "Polygon",
        "coordinates": [[(77.000, 28.000), (77.001, 28.000), (77.001, 28.001), (77.000, 28.001), (77.000, 28.000)]],
    }

    area_str = compute_area(poly_geo, crs="EPSG:4326")
    area_float = float(area_str)

    # Must be physical square meters around ~10,941 m²
    assert 10500.0 <= area_float <= 11500.0
    assert "." in area_str
    assert len(area_str.split(".")[1]) == 2


def test_crs_sensitive_calculation_comparison():
    """3. Test that CRS determines calculation mode (projected vs geographic)."""
    poly = {
        "type": "Polygon",
        "coordinates": [[(77.000, 28.000), (77.010, 28.000), (77.010, 28.010), (77.000, 28.010), (77.000, 28.000)]],
    }

    # EPSG:3857 or EPSG:32643 treats (77, 28) as planar meters -> 0.01 x 0.01 = 0.00 m²
    proj_area = compute_area(poly, crs="EPSG:3857")

    # EPSG:4326 treats (77, 28) as degrees -> converted to physical m² (~1.09 km² = ~1,094,000 m²)
    geo_area = compute_area(poly, crs="EPSG:4326")

    assert proj_area != geo_area
    assert float(geo_area) > 1000000.0


def test_invalid_and_degenerate_polygons_safe_fallback():
    """4. Safe fallback for invalid, open, degenerate, or self-intersecting polygons."""
    # Degenerate fewer than 3 vertices
    assert compute_area({"coordinates": [[(0, 0), (10, 10)]]}) == "0.00"

    # Self-intersecting bowtie ring
    bowtie = {
        "type": "Polygon",
        "coordinates": [[(0.0, 0.0), (10.0, 10.0), (0.0, 10.0), (10.0, 0.0), (0.0, 0.0)]],
    }
    assert compute_area(bowtie) == "0.00"

    # Invalid payload input types
    assert compute_area(None) == "0.00"
    assert compute_area({}) == "0.00"


def test_is_geographic_crs_helper():
    """5. Verify CRS classification helper."""
    assert is_geographic_crs("EPSG:4326") is True
    assert is_geographic_crs("WGS84") is True
    assert is_geographic_crs("OGC:CRS84") is True
    assert is_geographic_crs("EPSG:32643") is False
    assert is_geographic_crs("EPSG:3857") is False
    assert is_geographic_crs(None) is False
