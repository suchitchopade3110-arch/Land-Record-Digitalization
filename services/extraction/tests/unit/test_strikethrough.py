"""Unit tests for geometric strikethrough detection (FR-EXT-06)."""
from extraction.domain.strikethrough import detect_strikethrough


def test_detect_strikethrough_no_strokes():
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}
    assert detect_strikethrough(bbox, []) is False


def test_detect_strikethrough_with_intersecting_stroke():
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}
    page_strokes = [{"x1": 5.0, "y1": 15.0, "x2": 70.0, "y2": 15.0}]
    assert detect_strikethrough(bbox, page_strokes) is True


def test_detect_strikethrough_non_intersecting_stroke():
    bbox = {"x": 10.0, "y": 10.0, "w": 50.0, "h": 20.0}
    page_strokes = [{"x1": 100.0, "y1": 100.0, "x2": 200.0, "y2": 100.0}]
    assert detect_strikethrough(bbox, page_strokes) is False
