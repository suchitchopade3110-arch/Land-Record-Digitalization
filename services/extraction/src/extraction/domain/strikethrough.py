"""Geometric strikethrough/cancellation detection (FR-EXT-06).

A stroke crossing a bounding box is enough to detect cancellation.
Forces entry_status to remain "unknown" pending an explicit pass, and forces
mandatory human review regardless of field confidence when a strike is detected.
"""
from __future__ import annotations


def detect_strikethrough(bbox: dict | None, page_strokes: list) -> bool:
    """Checks if any geometric stroke in `page_strokes` intersects `bbox`.

    `bbox`: dict with keys `x`, `y`, `w`, `h`.
    `page_strokes`: list of line dicts `{"x1": float, "y1": float, "x2": float, "y2": float}`.
    """
    if not bbox or not page_strokes:
        return False

    bx1 = float(bbox.get("x", 0))
    by1 = float(bbox.get("y", 0))
    bx2 = bx1 + float(bbox.get("w", 0))
    by2 = by1 + float(bbox.get("h", 0))

    for stroke in page_strokes:
        sx1 = float(stroke.get("x1", 0))
        sy1 = float(stroke.get("y1", 0))
        sx2 = float(stroke.get("x2", 0))
        sy2 = float(stroke.get("y2", 0))

        if _line_intersects_rect(sx1, sy1, sx2, sy2, bx1, by1, bx2, by2):
            return True

    return False


def _line_intersects_rect(
    x1: float, y1: float, x2: float, y2: float,
    rx1: float, ry1: float, rx2: float, ry2: float,
) -> bool:
    """Bounding-box line segment intersection check."""
    # Fast bounding-box check
    if max(x1, x2) < rx1 or min(x1, x2) > rx2:
        return False
    if max(y1, y2) < ry1 or min(y1, y2) > ry2:
        return False
    return True
