"""GET /conflicts, GET /conflicts/{id}, POST /conflicts/{id}/assign,
POST /conflicts/{id}/transition — FR-CFL-01-05 (P3-10/12, P6/T1-01).
Conflict register (M10). `actor` is resolved from identity (`identity.actor`)
via `require_permission` RBAC dependencies.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.auth import Permission, require_permission
from backend.api.deps import get_session
from backend.api.serializers import ConflictPublicView, conflict_to_public_view
from backend.domain.access_control import Identity
from backend.domain.conflict_register import InvalidConflictTransition, list_conflicts
from backend.domain.conflict_register import assign as assign_conflict
from backend.domain.conflict_register import transition as transition_conflict
from backend.models.entities import Conflict

router = APIRouter(tags=["conflicts"])

_require_conflict_read = require_permission(Permission.CONFLICT_READ)
_require_conflict_assign = require_permission(Permission.CONFLICT_ASSIGN)
_require_conflict_transition = require_permission(Permission.CONFLICT_TRANSITION)


@router.get("/conflicts", response_model=list[ConflictPublicView])
def list_conflicts_route(
    state: str | None = Query(None),
    identity: Identity = Depends(_require_conflict_read),
    session: Session = Depends(get_session),
) -> list[ConflictPublicView]:
    return [conflict_to_public_view(c) for c in list_conflicts(session, state=state)]


@router.get("/conflicts/{conflict_id}", response_model=ConflictPublicView)
def get_conflict(
    conflict_id: str,
    identity: Identity = Depends(_require_conflict_read),
    session: Session = Depends(get_session),
) -> ConflictPublicView:
    conflict = session.get(Conflict, conflict_id)
    if conflict is None:
        raise HTTPException(status_code=404, detail="no such conflict")
    return conflict_to_public_view(conflict)


@router.post("/conflicts/{conflict_id}/assign", response_model=ConflictPublicView)
def assign_conflict_route(
    conflict_id: str,
    assignee: str = Body(..., embed=True),
    identity: Identity = Depends(_require_conflict_assign),
    session: Session = Depends(get_session),
) -> ConflictPublicView:
    try:
        conflict = assign_conflict(session, conflict_id, assignee=assignee, actor=identity.actor)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    session.commit()
    return conflict_to_public_view(conflict)


@router.post("/conflicts/{conflict_id}/transition", response_model=ConflictPublicView)
def transition_conflict_route(
    conflict_id: str,
    new_state: str = Body(...),
    resolution: str | None = Body(None),
    identity: Identity = Depends(_require_conflict_transition),
    session: Session = Depends(get_session),
) -> ConflictPublicView:
    """P3-12 / T1-01 — `resolution` required when `new_state == "resolved"`; both
    it and `actor` land in the audit trail (`conflict_register.transition`)."""
    try:
        conflict = transition_conflict(
            session, conflict_id, new_state=new_state, actor=identity.actor, resolution=resolution,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidConflictTransition as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    session.commit()
    return conflict_to_public_view(conflict)
