"""GET /conflicts, GET /conflicts/{id}, POST /conflicts/{id}/assign,
POST /conflicts/{id}/transition — FR-CFL-01-05 (P3-10/12). Conflict
register (M10). `actor` accepted explicitly pending FR-SEC-01, same note
as `backend.api.review_tasks`.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.deps import get_session
from backend.api.serializers import ConflictPublicView, conflict_to_public_view
from backend.domain.conflict_register import InvalidConflictTransition, list_conflicts
from backend.domain.conflict_register import assign as assign_conflict
from backend.domain.conflict_register import transition as transition_conflict
from backend.models.entities import Conflict

router = APIRouter(tags=["conflicts"])


@router.get("/conflicts", response_model=list[ConflictPublicView])
def list_conflicts_route(
    state: str | None = Query(None), session: Session = Depends(get_session)
) -> list[ConflictPublicView]:
    return [conflict_to_public_view(c) for c in list_conflicts(session, state=state)]


@router.get("/conflicts/{conflict_id}", response_model=ConflictPublicView)
def get_conflict(conflict_id: str, session: Session = Depends(get_session)) -> ConflictPublicView:
    conflict = session.get(Conflict, conflict_id)
    if conflict is None:
        raise HTTPException(status_code=404, detail="no such conflict")
    return conflict_to_public_view(conflict)


@router.post("/conflicts/{conflict_id}/assign", response_model=ConflictPublicView)
def assign_conflict_route(
    conflict_id: str, assignee: str = Body(...), actor: str = Body(...), session: Session = Depends(get_session),
) -> ConflictPublicView:
    try:
        conflict = assign_conflict(session, conflict_id, assignee=assignee, actor=actor)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    session.commit()
    return conflict_to_public_view(conflict)


@router.post("/conflicts/{conflict_id}/transition", response_model=ConflictPublicView)
def transition_conflict_route(
    conflict_id: str,
    new_state: str = Body(...),
    actor: str = Body(...),
    resolution: str | None = Body(None),
    session: Session = Depends(get_session),
) -> ConflictPublicView:
    """P3-12 — `resolution` required when `new_state == "resolved"`; both
    it and `actor` land in the audit trail (`conflict_register.transition`)."""
    try:
        conflict = transition_conflict(session, conflict_id, new_state=new_state, actor=actor, resolution=resolution)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except InvalidConflictTransition as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    session.commit()
    return conflict_to_public_view(conflict)
