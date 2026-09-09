"""POST /documents — FR-ING-01/02/04/05. Client uploads one document, gets
a job id back immediately — this handler never blocks on page split or
triage (that's `backend.workers.ingestion_consumer`, running off
`INGESTION_QUEUE`). It never blocks on the broker being reachable either
(`backend.domain.backpressure`): custody and the outbox write only need
Postgres, which is what makes §07's "ingestion may queue during an outage,
it must not reject" true by construction rather than by a retry loop here.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from backend.domain.ingest import (
    MissingMandatoryBatchField,
    UnsupportedMediaType,
    get_or_create_batch,
    ingest_document,
    validate_batch_metadata,
    validate_mime,
)
from backend.domain.unit_table import NoUnitTableForDistrict, unit_table_for_district
from backend.models.base import session_factory

router = APIRouter(tags=["documents"])


def get_session():
    factory = session_factory()
    with factory() as session:
        yield session


@router.post("/documents", status_code=202)
def create_document(
    file: UploadFile,
    district: str = Form(...),
    tehsil: str | None = Form(None),
    village: str | None = Form(None),
    series: str | None = Form(None),
    custodian: str | None = Form(None),
    scanning_date: datetime | None = Form(None),  # noqa: B008 — FastAPI's Form() default-arg idiom, see ruff.toml
    batch_id: str | None = Form(None),
    session: Session = Depends(get_session),
) -> dict:
    mime = file.content_type or "application/octet-stream"

    # Reject before any DB write: unsupported MIME, missing mandatory
    # metadata, and the demo's "no unit table for this district" block are
    # all doors, not clean-up — nothing should be half-created because one
    # of them fired.
    try:
        validate_mime(mime)
    except UnsupportedMediaType as e:
        raise HTTPException(status_code=422, detail={"reason_code": e.reason_code, "mime": e.mime}) from e

    try:
        validate_batch_metadata(district=district)
    except MissingMandatoryBatchField as e:
        raise HTTPException(status_code=422, detail={"reason_code": e.reason_code, "message": str(e)}) from e

    # Demo increment ("scripted moment 2 — the refusal"): a district with
    # no configured unit table blocks rather than guesses.
    try:
        unit_table_for_district(district)
    except NoUnitTableForDistrict as e:
        raise HTTPException(status_code=422, detail={"reason_code": e.reason_code, "district": e.district}) from e

    batch = get_or_create_batch(
        session, batch_id=batch_id, district=district, tehsil=tehsil, village=village,
        series=series, custodian=custodian, scanning_date=scanning_date,
    )
    result = ingest_document(session, batch=batch, stream=file.file, mime=mime)
    session.commit()
    return {
        "job_id": result.document_id,
        "batch_id": result.batch_id,
        "status": "accepted",
        "deduped": not result.created,
    }
