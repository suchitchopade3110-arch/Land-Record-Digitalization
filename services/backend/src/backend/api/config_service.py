"""GET /config/{scope}/{key} — FR-CFG-01/02, P5-03.
contracts/openapi/config-service.suchit.yaml
Callers: Shree, Shruthi, Tharun. See API-Contracts-and-Interfaces.md §4.1.

Two forms, per the contract:
  - unpinned: `GET /config/{scope}/{key}` — "what is effective now". Only
    the triage router and the impact preview may call this form
    (CLAUDE.md invariant 3 / FR-CFG-02) — everything else downstream of
    triage reads a pinned envelope instead.
  - pinned: `GET /config/{scope}/{key}?config_version=vN` — that exact,
    immutable row, forever, regardless of what supersedes it. This is the
    form the pipeline actually uses.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.domain.config_versions import (
    ConfigNotFound,
    as_response,
    get_effective_config,
    get_pinned_config,
)
from backend.models.base import session_factory

router = APIRouter(tags=["config"])


def get_session():
    factory = session_factory()
    with factory() as session:
        yield session


@router.get("/config/{scope}/{key}")
def get_config(
    scope: str,
    key: str,
    config_version: str | None = Query(None, description="Pin to this exact ConfigVersion id"),
    session: Session = Depends(get_session),
) -> dict:
    try:
        if config_version is not None:
            row = get_pinned_config(session, scope, key, config_version)
        else:
            row = get_effective_config(session, scope, key)
    except ConfigNotFound:
        raise HTTPException(status_code=404, detail="No config value found for this scope/key.")
    return as_response(row)
