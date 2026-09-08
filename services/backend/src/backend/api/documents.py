"""POST /documents — TODO: FR-ING-01-04. Ingest entrypoint (M1).
Client uploads a document, gets a job id back, never blocks on processing.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["documents"])


@router.post("/documents")
def create_document():
    # TODO: FR-ING-01 — hash (SHA-256), immutable object store, page split,
    # enqueue to INGESTION_QUEUE, return job id without blocking.
    raise HTTPException(status_code=501, detail="TODO: FR-ING-01..04 not implemented")
