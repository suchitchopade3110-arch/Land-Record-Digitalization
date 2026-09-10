"""P4-02/FR-PUB-02 — record and navigate field provenance: given a
published field, resolve back to page image + bbox (through whatever
preprocessing transform was applied), engine/model/config identity, and
the full edit history that produced its current value.

See `backend.models.entities.FieldProvenance`'s docstring for why this
table does not duplicate what `Extraction` already stores, and why "edit
history with actors" reuses `Correction` rather than a new table.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.preprocessing import AffineTransform, map_point_to_original
from backend.models.entities import Correction, Extraction, FieldProvenance, Page


def record_provenance(
    session: Session,
    *,
    extraction: Extraction,
    document_id: str,
    page_split_transform: dict | None = None,
    deskew_transform: AffineTransform | dict | None = None,
) -> FieldProvenance:
    """Called once per `Extraction` row, at the same time the extraction
    itself is written (by whoever assembles it — Shree's lane in a real
    pipeline; a direct call in this repo's fixture-backed tests, same
    posture as every other cross-team seam PHASE2.md/PHASE3.md name).
    `deskew_transform` accepts either the dataclass `preprocessing.py`
    produces or an already-JSON-shaped dict, so a caller holding the
    dataclass from the same request doesn't have to hand-serialize it."""
    if isinstance(deskew_transform, AffineTransform):
        deskew_transform = {
            "angle_degrees": deskew_transform.angle_degrees,
            "original_size": list(deskew_transform.original_size),
            "processed_size": list(deskew_transform.processed_size),
        }
    provenance = FieldProvenance(
        extraction_id=extraction.id,
        document_id=document_id,
        page_split_transform=page_split_transform,
        deskew_transform=deskew_transform,
    )
    session.add(provenance)
    session.flush()
    return provenance


@dataclass(frozen=True)
class EditHistoryEntry:
    actor: str
    predicted: str | None
    corrected: str | None
    edit_distance: int | None
    at: str


@dataclass(frozen=True)
class ResolvedProvenance:
    """`field_name` (P4-10b fix) is what lets a caller mask `raw_value` and
    each `edit_history` entry's `predicted`/`corrected` the same way
    `mask_extraction_for_role` masks a record's current value — a
    personal-data field's edit history is provenance raw value in every
    sense ADR-007 cares about, so it must never leave this dataclass
    unmasked any more than the field's own `raw_value` does."""

    extraction_id: str
    document_id: str | None
    page_id: str
    field_name: str
    bbox: dict | None
    engine: str | None
    model_version: str | None
    config_version: str | None
    raw_value: str | None
    confidence: float | None
    page_split_transform: dict | None
    deskew_transform: dict | None
    edit_history: list[EditHistoryEntry]


class ExtractionNotFound(KeyError):
    pass


def resolve_provenance(session: Session, extraction_id: str) -> ResolvedProvenance:
    """T4.e — "from any published field, resolve to page image + bbox,
    assert rendered crop matches recorded coordinates, run against a page
    that went through deskew." This function is the resolution half;
    `original_bbox` below is the assertion half a caller (a test, or a
    crop-rendering endpoint) uses to check a rendered crop's coordinates
    against what deskew actually did to the page."""
    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise ExtractionNotFound(f"no Extraction with id={extraction_id!r}")
    page = session.get(Page, extraction.page_id)
    provenance = session.execute(
        select(FieldProvenance).where(FieldProvenance.extraction_id == extraction_id)
    ).scalar_one_or_none()

    corrections = session.execute(
        select(Correction)
        .where(Correction.extraction_id == extraction_id)
        .order_by(Correction.created_at.asc())
    ).scalars().all()

    return ResolvedProvenance(
        extraction_id=extraction.id,
        document_id=provenance.document_id if provenance else (page.document_id if page else None),
        page_id=extraction.page_id,
        field_name=extraction.field_name,
        bbox=extraction.bbox,
        engine=extraction.engine,
        model_version=extraction.model_version,
        config_version=extraction.config_version,
        raw_value=extraction.raw_value,
        confidence=extraction.calibrated_confidence or extraction.token_confidence,
        page_split_transform=provenance.page_split_transform if provenance else None,
        deskew_transform=provenance.deskew_transform if provenance else None,
        edit_history=[
            EditHistoryEntry(
                actor=c.actor, predicted=c.predicted, corrected=c.corrected,
                edit_distance=c.edit_distance, at=c.created_at.isoformat(),
            )
            for c in corrections
        ],
    )


def original_bbox_for(resolved: ResolvedProvenance) -> dict | None:
    """T4.e's assertion half — map the recorded (post-deskew) bbox's
    corners back to ORIGINAL scan coordinates via the recorded transform,
    so a caller can check a crop rendered from the *original* scan lines
    up with a bbox that was recorded against the *deskewed* image. Returns
    `None` when there's nothing to map (no bbox, or no transform recorded
    — i.e. the page was never deskewed, so `bbox` already IS
    original-space)."""
    if resolved.bbox is None or resolved.deskew_transform is None:
        return None
    t = resolved.deskew_transform
    transform = AffineTransform(
        angle_degrees=t["angle_degrees"],
        original_size=tuple(t["original_size"]),
        processed_size=tuple(t["processed_size"]),
    )
    x, y, w, h = resolved.bbox["x"], resolved.bbox["y"], resolved.bbox["w"], resolved.bbox["h"]
    corners = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
    mapped = [map_point_to_original(transform, cx, cy) for cx, cy in corners]
    xs = [p[0] for p in mapped]
    ys = [p[1] for p in mapped]
    return {"x": min(xs), "y": min(ys), "w": max(xs) - min(xs), "h": max(ys) - min(ys)}
