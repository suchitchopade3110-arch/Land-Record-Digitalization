"""P4-08/FR-SEC-02/FR-SEC-08 — the unmasked read as a separate, privileged
operation. Distinct from the normal masked read path (P4-10) in every way
the requirement names: its own function (never a query parameter on
`mask_extraction_for_role`), its own permission
(`Permission.RECORD_READ_UNMASKED`, checked by the route dependency, never
inline here), a mandatory non-empty purpose string, and its own audit
action type (`"field.unmasked_read"`, `backend.domain.audit_log
.record_unmasked_read`) distinct from an ordinary masked-read event (which
this repo does not even audit per-read — only the unmasked path is
sensitive enough to warrant one row per access, matching FR-SEC-08's own
framing that this is the exceptional, logged operation, not the default).
"""
from __future__ import annotations

from dataclasses import dataclass

from landmasking import apply as mask_apply
from sqlalchemy.orm import Session

from backend.domain.audit_log import record_unmasked_read
from backend.models.entities import Batch, Extraction, Page, SourceDocument


class MissingPurpose(ValueError):
    pass


class ExtractionNotFound(KeyError):
    pass


@dataclass(frozen=True)
class UnmaskedField:
    extraction_id: str
    field_name: str
    value: str | None
    raw_value: str | None
    crop_url: str | None


def read_unmasked_field(
    session: Session, *, extraction_id: str, actor: str, purpose: str, record_id: str, version: int,
) -> UnmaskedField:
    """`purpose` is required and non-empty (FR-SEC-08) — checked here, not
    only left to a client-side form validator, since this function is the
    actual security boundary. `record_id`/`version` are the field identity
    P4-07 requires on the audit entry (`(record_id, version, field_name)`)
    — passed in by the caller (the route) rather than resolved from
    `Extraction` here, since `Extraction` has no `record_id`/`version` of
    its own (it belongs to a `Page`, which does not carry which published
    `Record` version it ended up in) — the caller supplies the record
    context it's already navigating from.
    """
    if not purpose or not purpose.strip():
        raise MissingPurpose("an unmasked read requires a non-empty purpose string (FR-SEC-08)")

    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise ExtractionNotFound(f"no Extraction with id={extraction_id!r}")

    page = session.get(Page, extraction.page_id)
    district = None
    crop_url = None
    if page is not None:
        doc = session.get(SourceDocument, page.document_id)
        if doc is not None:
            batch = session.get(Batch, doc.batch_id)
            district = batch.district if batch else None
        if page.storage_uri and extraction.bbox is not None:
            from landstorage import get_store

            from backend.domain.review_policy import CROP_URL_TTL_SECONDS

            signed = get_store().sign_get(page.storage_uri, CROP_URL_TTL_SECONDS)
            crop_url = f"{signed}&bbox={extraction.bbox}" if "?" in signed else f"{signed}?bbox={extraction.bbox}"

    # `role="auditor_unmasked_read"` is the one distinguished value that
    # makes `landmasking.apply` return everything unmasked — never a
    # normal role's default view (see that module's own docstring).
    unmasked = mask_apply(
        value=extraction.canonical_value,
        raw_value=extraction.raw_value,
        crop_uri=crop_url,
        field_class=extraction.field_name,
        role="auditor_unmasked_read",
    )

    record_unmasked_read(
        session, actor=actor, record_id=record_id, version=version,
        field_name=extraction.field_name, purpose=purpose, district=district,
    )
    session.flush()

    return UnmaskedField(
        extraction_id=extraction.id,
        field_name=extraction.field_name,
        value=unmasked.value,
        raw_value=unmasked.raw_value,
        crop_url=unmasked.crop_uri,
    )
