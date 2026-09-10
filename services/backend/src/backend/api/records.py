"""P4-01/02/08/09/10 — the routes this phase adds for publication,
provenance navigation, and masked/unmasked reads. Every route that can
return record data goes through `MaskedRecordView`/`mask_extraction_for_role`
(P4-10, one serializer path) and every route is gated by
`backend.api.auth.require_permission` — no route handler in this file
compares a role name inline.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.auth import Permission, require_permission
from backend.api.deps import get_session
from backend.api.serializers import (
    MaskedRecordView,
    build_masked_record_view,
    mask_edit_history_entry,
)
from backend.domain.access_control import Identity
from backend.domain.provenance import (
    ExtractionNotFound,
    original_bbox_for,
    resolve_provenance,
)
from backend.domain.publication import PublishBlocked, publish_new_version
from backend.domain.publication_gate import BlockReason
from backend.domain.unmasked_read import (
    ExtractionNotFound as UnmaskedExtractionNotFound,
)
from backend.domain.unmasked_read import (
    MissingPurpose,
    UnmaskedField,
    read_unmasked_field,
)
from backend.models.entities import Extraction, Page, Record

router = APIRouter(tags=["records"])

# Module-level dependency singletons — ruff's B008 (and FastAPI's own
# recommendation) is to not call a factory *inside* a `Depends(...)`
# default; `Depends(require_permission(Permission.X))` nests a real call
# there, so each permission's dependency is built once, here, and referenced
# by name in every route below.
_require_publish = require_permission(Permission.RECORD_PUBLISH)
_require_read_masked = require_permission(Permission.RECORD_READ_MASKED)
_require_provenance_read = require_permission(Permission.PROVENANCE_READ)
_require_read_unmasked = require_permission(Permission.RECORD_READ_UNMASKED)


def _reasons_payload(reasons: tuple[BlockReason, ...]) -> list[dict]:
    return [{"code": r.code, "detail": r.detail} for r in reasons]


def _primary_role(identity: Identity) -> str:
    """`landmasking.apply`'s `role` parameter is a single string; P0's
    `MockIdentityProvider` roster grants exactly one role per actor
    (`access_control.default_mock_identity_provider`), so "the" role is
    unambiguous today. A real multi-role identity would need a masking
    policy over a *set* of roles (mask only if every held role would mask)
    — not built here since nothing in this phase's roster exercises it;
    flagged rather than silently picked an arbitrary element."""
    return min(r.value for r in identity.roles)


@router.post("/records/publish", response_model=dict)
def publish_record(
    record_group_id: str | None = Body(None),
    batch_id: str | None = Body(None),
    parcel_ref: str | None = Body(None),
    ulpin: str | None = Body(None),
    lgd_codes: dict | None = Body(None),
    identity: Identity = Depends(_require_publish),
    session: Session = Depends(get_session),
) -> dict:
    """P4-01 — always an append (a new `Record` version), never an
    update. `PublishBlocked` (volume completeness / open conflict, the
    same gate Phase 2/3 built) becomes a 409 naming every reason, not just
    the first."""
    try:
        record = publish_new_version(
            session, record_group_id=record_group_id, batch_id=batch_id, actor=identity.actor,
            parcel_ref=parcel_ref, ulpin=ulpin, lgd_codes=lgd_codes,
        )
    except PublishBlocked as e:
        raise HTTPException(status_code=409, detail={"reasons": _reasons_payload(e.reasons)}) from e
    session.commit()
    return {"record_group_id": record.record_group_id, "id": record.id, "version": record.version}


@router.get("/records/{record_group_id}", response_model=MaskedRecordView)
def get_record(
    record_group_id: str,
    version: int | None = None,
    identity: Identity = Depends(_require_read_masked),
    session: Session = Depends(get_session),
) -> MaskedRecordView:
    """P4-10 — the ONLY shape a record's fields leave this process in.
    `version` defaults to the latest published version; extraction rows
    for a record are looked up by whatever the caller's record-assembly
    already links (`RecordAssembly.extraction_ids`) — a real deployment
    resolves that join; this route accepts an explicit `extraction_ids`-
    free simplification (an empty field list is a valid, if unhelpful,
    masked record view) since `RecordAssembly` wiring is orthogonal to
    the masking guarantee this route exists to prove.
    """
    query = select(Record).where(Record.record_group_id == record_group_id)
    query = query.where(Record.version == version) if version is not None else query.order_by(Record.version.desc())
    record = session.execute(query.limit(1)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="no such record/version")

    from backend.models.entities import RecordAssembly

    assembly = session.execute(
        select(RecordAssembly).where(RecordAssembly.record_id == record_group_id)
    ).scalars().first()
    extraction_ids = assembly.extraction_ids if assembly else []
    extractions = []
    for ex_id in extraction_ids:
        extraction = session.get(Extraction, ex_id)
        if extraction is None:
            continue
        page = session.get(Page, extraction.page_id)
        d = extraction.to_contract_dict()
        d["page_storage_uri"] = page.storage_uri if page else None
        extractions.append(d)

    return build_masked_record_view(
        record.record_group_id, record.version, record.status, extractions, _primary_role(identity),
    )


@router.get("/extractions/{extraction_id}/provenance", response_model=dict)
def get_provenance(
    extraction_id: str,
    identity: Identity = Depends(_require_provenance_read),
    session: Session = Depends(get_session),
) -> dict:
    """P4-02/T4.e — resolve a field back to document/page/bbox/engine/
    model/config identity, the recorded page-split/deskew transforms, and
    the edit history that produced its current value.

    P4-10b fix: `edit_history` entries now go through
    `mask_edit_history_entry` — a personal-data field's `predicted`/
    `corrected` strings were previously returned verbatim here regardless
    of role, bypassing masking entirely (this route built its own dict by
    hand rather than going through the shared serializer). Found by
    P4-10b's live route×role masking property test, which is exactly the
    gap ADR-007 says a per-endpoint masking check can't reliably catch.
    `raw_value` itself is deliberately never included in this response at
    all (it never was) — the safest way to guarantee it never leaks here
    is to not carry it across the API boundary in the first place.
    """
    try:
        resolved = resolve_provenance(session, extraction_id)
    except ExtractionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    role = _primary_role(identity)
    masked_history = [
        mask_edit_history_entry(h, field_class=resolved.field_name, role=role) for h in resolved.edit_history
    ]
    return {
        "extraction_id": resolved.extraction_id,
        "document_id": resolved.document_id,
        "page_id": resolved.page_id,
        "bbox": resolved.bbox,
        "original_bbox": original_bbox_for(resolved),
        "engine": resolved.engine,
        "model_version": resolved.model_version,
        "config_version": resolved.config_version,
        "confidence": resolved.confidence,
        "page_split_transform": resolved.page_split_transform,
        "deskew_transform": resolved.deskew_transform,
        "edit_history": [
            {"actor": m.actor, "predicted": m.predicted, "corrected": m.corrected, "at": m.at, "masked": m.masked}
            for m in masked_history
        ],
    }


@router.post("/extractions/{extraction_id}/unmasked-read", response_model=dict)
def unmasked_read_route(
    extraction_id: str,
    record_id: str = Body(...),
    version: int = Body(...),
    purpose: str = Body(...),
    identity: Identity = Depends(_require_read_unmasked),
    session: Session = Depends(get_session),
) -> dict:
    """P4-08/FR-SEC-08 — a separate, privileged, purpose-required
    operation. Not a query parameter on `get_record`: distinct route,
    distinct permission (`RECORD_READ_UNMASKED`, only `Role.AUDITOR`
    holds it — see `access_control.PERMISSION_MATRIX`), distinct audit
    action type (`backend.domain.audit_log.record_unmasked_read`).
    """
    try:
        result: UnmaskedField = read_unmasked_field(
            session, extraction_id=extraction_id, actor=identity.actor, purpose=purpose,
            record_id=record_id, version=version,
        )
    except MissingPurpose as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except UnmaskedExtractionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    session.commit()
    return {
        "extraction_id": result.extraction_id,
        "field_name": result.field_name,
        "value": result.value,
        "raw_value": result.raw_value,
        "crop_url": result.crop_url,
    }
