"""P3-09/FR-CNF-06/07. Tharun writes the sampling decision and `stratum`
(`backend.domain.decision._audit_sample`, at routing time); this module
writes the one field this phase owns — `officer_verdict` — and derives
`agreed` at submit. Never tells the officer which stream the task came
from, before or after: this function receives a `ReviewTask`/`AuditSample`
pair the caller (`backend.domain.review_workflow.submit`) already looked
up server-side, and returns nothing that names `source_stream`.
"""
from __future__ import annotations

from landaudit import append as audit_append
from sqlalchemy.orm import Session

from backend.models.entities import AuditSample

VALID_VERDICTS = {"agree", "disagree"}


def record_officer_verdict(session: Session, audit_sample: AuditSample, *, verdict: str, actor: str) -> AuditSample:
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"officer_verdict must be one of {VALID_VERDICTS}, got {verdict!r}")
    audit_sample.officer_verdict = verdict
    audit_sample.agreed = verdict == "agree"
    session.flush()
    audit_append(
        session, actor=actor, action="audit_sample.verdict_recorded",
        subject=audit_sample.extraction_id, purpose="audit_verdict",
    )
    return audit_sample
