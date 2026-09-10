"""GET/POST /config/{scope}/{key} — FR-CFG-01/02/03, P5-03/P5-02b.
contracts/openapi/config-service.suchit.yaml
Callers: Shree, Shruthi, Tharun. See API-Contracts-and-Interfaces.md §4.1.

Two GET forms, per the contract:
  - unpinned: `GET /config/{scope}/{key}` — "what is effective now". Only
    the triage router and the impact preview may call this form
    (CLAUDE.md invariant 3 / FR-CFG-02) — everything else downstream of
    triage reads a pinned envelope instead.
  - pinned: `GET /config/{scope}/{key}?config_version=vN` — that exact,
    immutable row, forever, regardless of what supersedes it. This is the
    form the pipeline actually uses.

P5-02b — `POST /config/{scope}/{key}` is new. It is not in
`contracts/openapi/config-service.suchit.yaml`, which is frozen and
documents the GET form only; adding a write path there would be a
`contracts/` edit, so this route is documented in
`gateway/route_registry.yaml` instead (not frozen) and reuses exactly the
§4.1 response shape on success (`key, value, config_version,
effective_from` — no new fields), per the P5-02b task's own instruction.
Gated by `Permission.CONFIG_WRITE` (administrator only, P4-09's matrix);
the authenticated caller is the `author` — never a body field a client
could use to claim someone else's identity — `approver` is the only actor
named in the request body.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.auth import Permission, require_permission
from backend.domain.access_control import Identity
from backend.domain.config_versions import (
    AuthorEqualsApprover,
    ConfigNotFound,
    as_response,
    get_effective_config,
    get_pinned_config,
    write_config_version,
)
from backend.models.base import session_factory

router = APIRouter(tags=["config"])

_require_config_write = require_permission(Permission.CONFIG_WRITE)


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


@router.post("/config/{scope}/{key}")
def write_config(
    scope: str,
    key: str,
    value: dict = Body(...),
    effective_from: datetime = Body(...),
    approver: str = Body(...),
    identity: Identity = Depends(_require_config_write),
    session: Session = Depends(get_session),
) -> dict:
    """P5-02b — FR-CFG-01/03. `author` is the authenticated caller
    (`identity.actor`); `approver` must be a distinct named actor or the
    write is rejected with 422 before anything is persisted — the DB
    `CHECK (author <> approver)` (P5-01) is the backstop, not the primary
    enforcement point. On success: supersedes the prior head for
    (scope, key), audits both actors, and publishes the invalidation
    event any subscribed `ConfigClient` picks up on its next read
    (P5-04)."""
    try:
        row = write_config_version(
            session, scope=scope, key=key, value=value, effective_from=effective_from,
            author=identity.actor, approver=approver,
        )
    except AuthorEqualsApprover as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    session.commit()
    return as_response(row)
