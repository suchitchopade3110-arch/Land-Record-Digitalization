"""Field masking enforced at the serialization layer, not the UI — FR-SEC-02.

Three places a masked field's personal data must never leak (ADR-007):
  1. the current value
  2. the provenance raw_value
  3. the linked source crop
All three go through `landmasking.apply` together — see ADR-007. Also:
`ReviewTask.source_stream` must never leave this layer (FR-REV-11) —
`ReviewTaskPublicView` structurally omits the field rather than filtering
it out at serialization time, so there is no dict key to forget to strip.
"""
from __future__ import annotations

from typing import Any

from landmasking import apply as mask_apply
from pydantic import BaseModel


class ReviewTaskPublicView(BaseModel):
    """The only shape a `ReviewTask` may take once it leaves this process.
    No `source_stream` field exists on this model at all — FR-REV-11 is
    satisfied structurally, not by remembering to `del` a dict key."""

    id: str
    extraction_id: str
    reason: str | None
    assignee: str | None
    opened_at: str
    closed_at: str | None
    cluster_id: str | None
    cluster_size: int | None
    hour_into_session: int | None


def strip_review_task_internals(review_task: Any) -> ReviewTaskPublicView:
    """FR-REV-11 — build the public view from a `ReviewTask` ORM row or
    dict. `source_stream` is read here (to decide nothing — it's simply
    never copied) and then never touched again in this call."""
    get = (lambda k: getattr(review_task, k)) if not isinstance(review_task, dict) else review_task.get

    def _iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    return ReviewTaskPublicView(
        id=get("id"),
        extraction_id=get("extraction_id"),
        reason=get("reason"),
        assignee=get("assignee"),
        opened_at=_iso(get("opened_at")),
        closed_at=_iso(get("closed_at")),
        cluster_id=get("cluster_id"),
        cluster_size=get("cluster_size"),
        hour_into_session=get("hour_into_session"),
    )


class MaskedExtractionView(BaseModel):
    id: str
    page_id: str
    field_name: str
    value: str | None
    raw_value: str | None
    crop_uri: str | None
    entry_status: str
    masked: bool


def mask_extraction_for_role(extraction: dict[str, Any], role: str) -> MaskedExtractionView:
    """FR-SEC-02 — mask value, provenance.raw_value, and crop_uri together,
    never independently, based on `role`. `extraction["field_name"]`
    doubles as the field class `landmasking` checks against
    `PERSONAL_DATA_FIELD_CLASSES` — this schema has no separate field-class
    taxonomy at P0 (single pilot document type, FR-EXT-01).
    """
    crop_uri = (
        f"/pages/{extraction['page_id']}/crop?bbox={extraction['bbox']}"
        if extraction.get("bbox") is not None
        else None
    )
    masked = mask_apply(
        value=extraction.get("canonical_value"),
        raw_value=extraction.get("raw_value"),
        crop_uri=crop_uri,
        field_class=extraction["field_name"],
        role=role,
    )
    return MaskedExtractionView(
        id=extraction["id"],
        page_id=extraction["page_id"],
        field_name=extraction["field_name"],
        value=masked.value,
        raw_value=masked.raw_value,
        crop_uri=masked.crop_uri,
        entry_status=extraction["entry_status"],
        masked=masked.masked,
    )
