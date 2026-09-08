"""GET /config/{scope}/{key} — TODO: FR-CFG-01/02.
contracts/openapi/config-service.suchit.yaml
Callers: Shree, Shruthi, Tharun. See API-Contracts-and-Interfaces.md §4.1.
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["config"])


@router.get("/config/{scope}/{key}")
def get_config(scope: str, key: str):
    # TODO: FR-CFG-01 — read the active ConfigVersion for (scope, key).
    raise HTTPException(status_code=501, detail="TODO: FR-CFG-01 not implemented")
