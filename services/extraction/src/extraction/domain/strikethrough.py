"""Geometric strikethrough/cancellation detection. TODO: FR-EXT-06 (P0).
No labels needed — a stroke crossing a bounding box is enough. Forces
entry_status to remain "unknown" pending an explicit pass, and forces
mandatory review regardless of field confidence when a strike is detected.
"""


def detect_strikethrough(bbox: dict, page_strokes: list) -> bool:
    raise NotImplementedError("TODO: FR-EXT-06 not implemented")
