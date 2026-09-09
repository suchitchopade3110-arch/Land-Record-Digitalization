"""FR-EXT-06/07 — `Extraction.entry_status` defaults to `unknown`, NEVER
`live` (enforced as a DB column default + CHECK enum, see
`backend.models.entities.Extraction`). This module is the *only*
application-level path that transitions a row to `live`, and it always
writes an audit event in the same transaction — invariant 2, CLAUDE.md.
Nothing else in this codebase should set `entry_status = "live"` directly;
a `grep -rn 'entry_status.*=.*"live"'` outside this file is a review
finding.
"""
from __future__ import annotations

from landaudit import append as audit_append
from sqlalchemy.orm import Session

from backend.models.entities import Extraction

VALID_TRANSITIONS_TO_LIVE = {"unknown"}  # only an unclassified entry may become live


class InvalidEntryStatusTransition(ValueError):
    pass


def mark_live(session: Session, extraction_id: str, *, actor: str, reason_code: str) -> Extraction:
    """Classify an entry as live (the semantic half of FR-EXT-06/07, once
    labelled examples exist per PRD §M3 — this is the mechanism, not a
    claim that the classifier behind it is built). Writes an `audit_entry`
    row in the same session/transaction, so "an extraction became live"
    and "that transition is on the record" commit together or not at all.
    """
    extraction = session.get(Extraction, extraction_id)
    if extraction is None:
        raise KeyError(f"no Extraction with id={extraction_id!r}")
    if extraction.entry_status not in VALID_TRANSITIONS_TO_LIVE:
        raise InvalidEntryStatusTransition(
            f"cannot transition entry_status from {extraction.entry_status!r} to 'live' "
            f"(only {sorted(VALID_TRANSITIONS_TO_LIVE)} may become live)"
        )

    extraction.entry_status = "live"
    audit_append(
        session,
        actor=actor,
        action="extraction.entry_status.mark_live",
        subject=extraction_id,
        purpose=reason_code,
    )
    session.flush()
    return extraction
