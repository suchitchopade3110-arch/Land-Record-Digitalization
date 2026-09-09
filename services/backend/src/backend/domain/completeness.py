"""FR-ING-08 — `VolumeIndex`: reconstruct a register's expected page
sequence from entry-number continuity / khasra sequence, expressed here as
each page's `index_position`. That value is populated by Shree's page
parsing (M3/M4), never derived by this module — `record_index_position`
is the one consumption point for it, standing in for the queue message
Shree's lane would eventually publish once that payload/contract exists
(no such contract is proposed here — see PHASE2.md — only the transport
is a stand-in, not the completeness logic itself).

A batch-level alert names the specific missing index positions
(`current_alert`); publication from a batch with an open, unacknowledged
gap is blocked via `backend.domain.publication_gate`, not by this module
directly.
"""
from __future__ import annotations

from dataclasses import dataclass

from landaudit import append as audit_append
from sqlalchemy.orm import Session

from backend.models.entities import Page, SourceDocument, VolumeIndex


def record_index_position(session: Session, *, page_id: str, index_position: int) -> None:
    page = session.get(Page, page_id)
    if page is None:
        raise KeyError(f"no Page with id={page_id!r}")
    page.index_position = index_position
    session.flush()


def rebuild(session: Session, batch_id: str) -> VolumeIndex:
    """Recompute a batch's `VolumeIndex` from its pages' current
    `index_position`s. Get-or-create by `batch_id` — idempotent, never
    accumulates a second row for the same batch on repeated rebuilds
    (called every time a new `index_position` arrives).
    """
    observed = sorted(
        p.index_position
        for p in (
            session.query(Page)
            .join(SourceDocument, Page.document_id == SourceDocument.id)
            .filter(SourceDocument.batch_id == batch_id)
            .all()
        )
        if p.index_position is not None
    )

    if observed:
        expected = list(range(observed[0], observed[-1] + 1))
        observed_set = set(observed)
        gaps = [i for i in expected if i not in observed_set]
    else:
        expected, gaps = [], []

    volume_index = session.query(VolumeIndex).filter_by(batch_id=batch_id).first()
    if volume_index is None:
        volume_index = VolumeIndex(batch_id=batch_id)
        session.add(volume_index)

    volume_index.expected_sequence = expected
    volume_index.observed_sequence = observed
    volume_index.gaps = gaps

    if gaps:
        if volume_index.state != "acknowledged":
            volume_index.state = "gap_detected"
            audit_append(
                session, actor="system:completeness", action="volume_index.gap_detected",
                subject=batch_id, purpose="FR-ING-08",
            )
    else:
        volume_index.state = "complete" if expected else "pending"

    session.flush()
    return volume_index


def acknowledge_gap(session: Session, batch_id: str, *, actor: str, reason_code: str | None = None) -> VolumeIndex:
    """FR-ING-08 — an operator resolves or acknowledges an open gap,
    unblocking publication for the batch (`publication_gate`). Does not
    resolve the gap's `gaps` list itself (the missing pages may genuinely
    never arrive) — it records that a human has seen and accepted this,
    the same distinction `VolumeIndex.state`'s enum already draws between
    `gap_detected` and `acknowledged`.
    """
    volume_index = session.query(VolumeIndex).filter_by(batch_id=batch_id).first()
    if volume_index is None:
        raise KeyError(f"no VolumeIndex for batch_id={batch_id!r}")
    if volume_index.state != "gap_detected":
        raise ValueError(f"cannot acknowledge a VolumeIndex in state {volume_index.state!r}")
    volume_index.state = "acknowledged"
    audit_append(
        session, actor=actor, action="volume_index.gap_acknowledged", subject=batch_id, purpose=reason_code,
    )
    session.flush()
    return volume_index


@dataclass(frozen=True)
class CompletenessAlert:
    batch_id: str
    missing_index_positions: list[int]


def current_alert(session: Session, batch_id: str) -> CompletenessAlert | None:
    """The dashboard-visible alert for a batch, or `None` if there is no
    open gap (no gap at all, or one already acknowledged)."""
    volume_index = session.query(VolumeIndex).filter_by(batch_id=batch_id).first()
    if volume_index is None or not volume_index.gaps or volume_index.state == "acknowledged":
        return None
    return CompletenessAlert(batch_id=batch_id, missing_index_positions=list(volume_index.gaps))
