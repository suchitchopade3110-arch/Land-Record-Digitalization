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

from landmasking import ROLES_THAT_SEE_PERSONAL_DATA_UNMASKED
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


class ConflictPublicView(BaseModel):
    """P3-10 — "the entry alone must let a supervisor understand the
    dispute without opening the pipeline": every field a supervisor needs
    (records, rule, evidence, state, assignee, ageing) is here; nothing
    about `Conflict` needs withholding the way `ReviewTask.source_stream`
    does, so this is a plain projection, not a masking boundary."""

    id: str
    records: list[str]
    rule: str
    evidence: dict
    origin: str
    state: str
    assignee: str | None
    opened_at: str


def conflict_to_public_view(conflict: Any) -> ConflictPublicView:
    def _iso(value):
        return value.isoformat() if hasattr(value, "isoformat") else value

    return ConflictPublicView(
        id=conflict.id,
        records=conflict.records,
        rule=conflict.rule,
        evidence=conflict.evidence,
        origin=conflict.origin,
        state=conflict.state,
        assignee=conflict.assignee,
        opened_at=_iso(conflict.opened_at),
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


class MaskedRecordView(BaseModel):
    """P4-10/FR-SEC-02/ADR-007 — the one shape a `Record` may take once it
    leaves this process. Every route that can return record data
    (`GET /records/{id}`, provenance navigation, anywhere else a record's
    fields surface) builds this, never a hand-rolled dict — "masking is a
    property of a single serialization type, not a set of call sites."
    Wraps a list of `MaskedExtractionView` (already masks all three places
    per field — value/raw_value/crop_uri, ADR-007) plus the edit-history
    entries for each field, masked the identical way (a corrected/predicted
    value in a maker-checker'd personal-data field is exactly as much a
    leak as the current value).
    """

    record_id: str
    version: int
    status: str | None
    fields: list[MaskedExtractionView]


class MaskedEditHistoryEntry(BaseModel):
    actor: str
    predicted: str | None
    corrected: str | None
    at: str
    masked: bool


def mask_edit_history_entry(entry: Any, *, field_class: str, role: str) -> MaskedEditHistoryEntry:
    """The edit-history half of the "three places" masking rule
    (ADR-007's `raw_value`) — a `predicted`/`corrected` pair in a
    personal-data field's `Correction` history is provenance raw value in
    every sense that matters, so it goes through the identical
    `landmasking.apply` gate as the field's own `raw_value`."""
    masked = mask_apply(
        value=None, raw_value=entry.predicted, crop_uri=None, field_class=field_class, role=role,
    )
    masked_corrected = mask_apply(
        value=None, raw_value=entry.corrected, crop_uri=None, field_class=field_class, role=role,
    )
    return MaskedEditHistoryEntry(
        actor=entry.actor,
        predicted=masked.raw_value,
        corrected=masked_corrected.raw_value,
        at=entry.at,
        masked=masked.masked,
    )


def build_masked_record_view(
    record_id: str, version: int, status: str | None, extractions: list[dict[str, Any]], role: str,
) -> MaskedRecordView:
    """P4-10 — the entrypoint every record-reading route calls. Builds
    every field's masked view through `mask_extraction_for_role`, never
    lets a caller assemble the response dict by hand (which is exactly
    the "a set of call sites" ADR-007 says not to allow)."""
    return MaskedRecordView(
        record_id=record_id,
        version=version,
        status=status,
        fields=[mask_extraction_for_role(e, role) for e in extractions],
    )


def mask_extraction_for_role(extraction: dict[str, Any], role: str) -> MaskedExtractionView:
    """FR-SEC-02 — mask value, provenance.raw_value, and crop_uri together,
    never independently, based on `role`. `extraction["field_name"]`
    doubles as the field class `landmasking` checks against
    `PERSONAL_DATA_FIELD_CLASSES` — this schema has no separate field-class
    taxonomy at P0 (single pilot document type, FR-EXT-01).

    P4-10: "the crop is masked by refusing to issue the signed URL, not
    by blurring after the fact" — the signed URL is only ever *computed*
    when the masking decision already allows it (`is_personal_data`
    checked before calling `landstorage.sign_get`, not after); a masked
    field never has a real signed URL exist anywhere, even transiently,
    for `mask_apply` to then discard. `extraction["page_storage_uri"]` is
    optional — omitted (e.g. by a caller with no crop to offer) simply
    yields no `crop_uri` either way.
    """
    from landmasking import is_personal_data

    field_class = extraction["field_name"]
    would_be_masked = is_personal_data(field_class) and role not in ROLES_THAT_SEE_PERSONAL_DATA_UNMASKED

    crop_uri = None
    if not would_be_masked and extraction.get("bbox") is not None and extraction.get("page_storage_uri"):
        from landstorage import get_store

        from backend.domain.review_policy import CROP_URL_TTL_SECONDS

        signed = get_store().sign_get(extraction["page_storage_uri"], CROP_URL_TTL_SECONDS)
        crop_uri = f"{signed}&bbox={extraction['bbox']}" if "?" in signed else f"{signed}?bbox={extraction['bbox']}"

    masked = mask_apply(
        value=extraction.get("canonical_value"),
        raw_value=extraction.get("raw_value"),
        crop_uri=crop_uri,
        field_class=field_class,
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
