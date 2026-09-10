"""P0 Geometric Cancellation & Strikethrough Detection (FR-EXT-06).

Detects candidate strike/cancellation strokes using geometric line-segment vs bounding box
intersection math rather than semantic guessing.
Preserves entry_status semantics (unknown, live, cancelled, superseded, amended).
Ensures any stroke or ambiguous stroke near a value region routes for mandatory review (entry_status="unknown").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

VALID_ENTRY_STATUSES = {"unknown", "live", "cancelled", "superseded", "amended"}


@dataclass
class StrokeSegment:
    """Represents a geometric stroke segment extracted from page image vector/layout analysis."""

    x1: float
    y1: float
    x2: float
    y2: float
    thickness: float = 1.0
    confidence: float = 1.0

    def to_dict(self) -> dict[str, float]:
        return {
            "x1": self.x1,
            "y1": self.y1,
            "x2": self.x2,
            "y2": self.y2,
            "thickness": self.thickness,
            "confidence": self.confidence,
        }


@dataclass
class CancellationResult:
    """Result of geometric cancellation detection for a value bounding box."""

    is_cancelled: bool
    is_ambiguous: bool
    entry_status: str  # "unknown" | "live" | "cancelled" | "superseded" | "amended"
    intersecting_strokes: list[StrokeSegment] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_cancelled": self.is_cancelled,
            "is_ambiguous": self.is_ambiguous,
            "entry_status": self.entry_status,
            "intersecting_strokes": [s.to_dict() for s in self.intersecting_strokes],
            "rationale": self.rationale,
        }


def detect_strikethrough(bbox: dict | None, page_strokes: list) -> bool:
    """Checks if any geometric stroke in `page_strokes` intersects `bbox`.

    Returns True if an intersecting stroke or near-miss stroke is found.
    """
    res = classify_entry_cancellation(bbox=bbox, page_strokes=page_strokes)
    return res.is_cancelled or res.is_ambiguous


def classify_entry_cancellation(
    bbox: dict | None,
    page_strokes: list,
    current_status: str = "unknown",
    buffer_px: float = 4.0,
) -> CancellationResult:
    """Classifies an entry's cancellation state conservatively using exact line segment math.

    Rules:
    - Any stroke intersecting a field/value region -> is_cancelled=True, entry_status="cancelled".
    - Any stroke within buffer_px threshold (ambiguous stroke) -> is_ambiguous=True, entry_status="unknown".
    - Safe default behavior: "unknown" status NEVER silently becomes "live".
    """
    if current_status not in VALID_ENTRY_STATUSES:
        current_status = "unknown"

    if not bbox or not page_strokes:
        return CancellationResult(
            is_cancelled=False,
            is_ambiguous=False,
            entry_status=current_status,
            intersecting_strokes=[],
            rationale="No page strokes found intersecting or near bounding box.",
        )

    bx1 = float(bbox.get("x", 0))
    by1 = float(bbox.get("y", 0))
    bw = float(bbox.get("w", 0))
    bh = float(bbox.get("h", 0))
    bx2 = bx1 + bw
    by2 = by1 + bh

    # Expanded bounding box for buffer / ambiguous proximity check
    buff_bx1 = bx1 - buffer_px
    buff_by1 = by1 - buffer_px
    buff_bx2 = bx2 + buffer_px
    buff_by2 = by2 + buffer_px

    direct_hits: list[StrokeSegment] = []
    buffer_hits: list[StrokeSegment] = []

    for item in page_strokes:
        if isinstance(item, StrokeSegment):
            stroke = item
        elif isinstance(item, dict):
            stroke = StrokeSegment(
                x1=float(item.get("x1", 0)),
                y1=float(item.get("y1", 0)),
                x2=float(item.get("x2", 0)),
                y2=float(item.get("y2", 0)),
                thickness=float(item.get("thickness", 1.0)),
                confidence=float(item.get("confidence", 1.0)),
            )
        else:
            continue

        # 1. Direct line segment intersection check with exact bounding box
        if _line_intersects_rect(stroke.x1, stroke.y1, stroke.x2, stroke.y2, bx1, by1, bx2, by2):
            direct_hits.append(stroke)
        # 2. Buffer zone intersection check for ambiguous/near-miss strokes
        elif _line_intersects_rect(stroke.x1, stroke.y1, stroke.x2, stroke.y2, buff_bx1, buff_by1, buff_bx2, buff_by2):
            buffer_hits.append(stroke)

    if direct_hits:
        return CancellationResult(
            is_cancelled=True,
            is_ambiguous=False,
            entry_status="cancelled" if current_status != "superseded" else "superseded",
            intersecting_strokes=direct_hits,
            rationale=f"Detected {len(direct_hits)} direct geometric strikethrough stroke(s) across value bounding box.",
        )

    if buffer_hits:
        return CancellationResult(
            is_cancelled=False,
            is_ambiguous=True,
            entry_status="unknown",  # Route for mandatory human review
            intersecting_strokes=buffer_hits,
            rationale=f"Detected {len(buffer_hits)} ambiguous stroke(s) within {buffer_px}px proximity buffer. Routing for manual review.",
        )

    return CancellationResult(
        is_cancelled=False,
        is_ambiguous=False,
        entry_status=current_status,
        intersecting_strokes=[],
        rationale="No geometric strokes intersect value region.",
    )


def _line_intersects_rect(
    x1: float, y1: float, x2: float, y2: float,
    rx1: float, ry1: float, rx2: float, ry2: float,
) -> bool:
    """Exact line segment vs axis-aligned rectangle intersection test."""
    # Fast AABB test
    if max(x1, x2) < rx1 or min(x1, x2) > rx2:
        return False
    if max(y1, y2) < ry1 or min(y1, y2) > ry2:
        return False

    # Check if either segment endpoint is inside the rectangle
    if rx1 <= x1 <= rx2 and ry1 <= y1 <= ry2:
        return True
    if rx1 <= x2 <= rx2 and ry1 <= y2 <= ry2:
        return True

    # Check if segment intersects any of the 4 rectangle boundary edges
    p1, p2 = (x1, y1), (x2, y2)
    rect_edges = [
        ((rx1, ry1), (rx2, ry1)),  # Top
        ((rx2, ry1), (rx2, ry2)),  # Right
        ((rx2, ry2), (rx1, ry2)),  # Bottom
        ((rx1, ry2), (rx1, ry1)),  # Left
    ]

    for q1, q2 in rect_edges:
        if _segments_cross(p1, p2, q1, q2):
            return True

    return False


def _segments_cross(
    p1: tuple[float, float], p2: tuple[float, float],
    q1: tuple[float, float], q2: tuple[float, float],
) -> bool:
    """Determines whether 2D line segment p1-p2 crosses line segment q1-q2 using cross product orientations."""
    def ccw(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])

    return (ccw(p1, q1, q2) != ccw(p2, q1, q2)) and (ccw(p1, p2, q1) != ccw(p1, p2, q2))
