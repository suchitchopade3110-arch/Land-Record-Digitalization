"""Conflict register (M10). FR-CFL-01-03. Consumes verdicts from
Shruthi's validators, doesn't produce them — `open_conflict`'s `rule`/
`evidence` come from whatever validator (or downstream defect report,
FR-PUB-07) triggered the conflict; this module just records it.

Publication is BLOCKED for any record with an open conflict —
`is_publish_blocked` is a thin boolean wrapper over
`backend.domain.publication_gate.check_open_conflict`, the identical
block-publish primitive P2-08's volume-completeness gate uses ("one
implementation, two callers").
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from backend.domain.publication_gate import check_open_conflict
from backend.models.entities import Conflict


def open_conflict(session: Session, *, records: list[str], rule: str, evidence: dict, origin: str) -> Conflict:
    conflict = Conflict(records=records, rule=rule, evidence=evidence, origin=origin)
    session.add(conflict)
    session.flush()
    return conflict


def is_publish_blocked(session: Session, record_id: str) -> bool:
    return check_open_conflict(session, record_id) is not None
