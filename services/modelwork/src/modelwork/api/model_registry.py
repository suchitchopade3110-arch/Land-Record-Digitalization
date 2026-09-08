"""GET /models/{module}/active?stratum={stratum} — TODO: FR-LRN-04.
contracts/openapi/model-registry.tharun.yaml

Returns the currently promoted version only — the ONE place "current" is
resolved. Once pinned into a WorkEnvelope, nothing downstream calls this
again for that page (API-Contracts §4.2).
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["model-registry"])


@router.get("/models/{module}/active")
def get_active_model(module: str, writer_cluster_id: str | None = None, stratum: str | None = None):
    # TODO: FR-LRN-04 — look up the currently promoted ModelVersion for `module`,
    # resolving adapter_ref by writer_cluster_id if given.
    raise HTTPException(status_code=501, detail="TODO: FR-LRN-04 not implemented")
