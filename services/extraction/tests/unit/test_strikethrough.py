"""Unit tests for geometric cancellation & strikethrough detection (FR-EXT-06).

Tests:
1. No stroke
2. Stroke through text (direct geometric intersection)
3. Nearby non-intersecting stroke (outside buffer distance)
4. Multiple strokes (detecting multiple intersecting/cancelling lines)
5. Ambiguous stroke (near-miss stroke within buffer distance routing to unknown status)
"""
import pytest
from extraction.domain.strikethrough import (
    CancellationResult,
    StrokeSegment,
    classify_entry_cancellation,
    detect_strikethrough,
)


def test_no_stroke():
    """1. No stroke present."""
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}

    assert detect_strikethrough(bbox, []) is False

    res = classify_entry_cancellation(bbox, [], current_status="unknown")
    assert res.is_cancelled is False
    assert res.is_ambiguous is False
    assert res.entry_status == "unknown"  # Preserves default safely


def test_stroke_through_text():
    """2. Horizontal stroke passing through text bounding box."""
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}  # x: 10-60, y: 10-30
    page_strokes = [{"x1": 5.0, "y1": 20.0, "x2": 70.0, "y2": 20.0}]

    assert detect_strikethrough(bbox, page_strokes) is True

    res = classify_entry_cancellation(bbox, page_strokes)
    assert res.is_cancelled is True
    assert res.is_ambiguous is False
    assert res.entry_status == "cancelled"
    assert len(res.intersecting_strokes) == 1


def test_nearby_non_intersecting_stroke():
    """3. Nearby stroke completely outside bounding box and buffer distance."""
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}  # x: 10-60, y: 10-30
    # Far stroke at y=100
    page_strokes = [{"x1": 10.0, "y1": 100.0, "x2": 60.0, "y2": 100.0}]

    assert detect_strikethrough(bbox, page_strokes) is False

    res = classify_entry_cancellation(bbox, page_strokes, buffer_px=4.0)
    assert res.is_cancelled is False
    assert res.is_ambiguous is False
    assert len(res.intersecting_strokes) == 0


def test_multiple_strokes():
    """4. Multiple crossing strokes across text bounding box."""
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}
    page_strokes = [
        {"x1": 5.0, "y1": 15.0, "x2": 70.0, "y2": 15.0},  # Horizontal strike
        {"x1": 20.0, "y1": 5.0, "x2": 40.0, "y2": 35.0},  # Diagonal cross strike
    ]

    assert detect_strikethrough(bbox, page_strokes) is True

    res = classify_entry_cancellation(bbox, page_strokes)
    assert res.is_cancelled is True
    assert len(res.intersecting_strokes) == 2


def test_ambiguous_stroke():
    """5. Ambiguous near-miss stroke within buffer distance routes conservatively to unknown."""
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}  # x: 10-60, y: 10-30
    # Stroke passing at y=32.0 (2px below bottom boundary y=30.0, within buffer_px=4.0)
    page_strokes = [{"x1": 10.0, "y1": 32.0, "x2": 60.0, "y2": 32.0}]

    assert detect_strikethrough(bbox, page_strokes) is True

    res = classify_entry_cancellation(bbox, page_strokes, buffer_px=4.0)
    assert res.is_cancelled is False
    assert res.is_ambiguous is True
    assert res.entry_status == "unknown"  # Routed conservatively for mandatory human review!
    assert len(res.intersecting_strokes) == 1
