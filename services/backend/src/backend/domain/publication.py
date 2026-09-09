"""P4-01/FR-PUB-01 — publish a new `Record` VERSION. Never updates an
existing row (`backend.models.entities.Record`'s own docstring covers the
`record_group_id`/DB-trigger mechanics); this module is the write path
that produces the next version, gated by the same block-publish primitive
Phase 2/3 already built (`backend.domain.publication_gate.attempt_publish`
— "one implementation, two callers" now becomes three: volume
completeness, conflict register, and this phase's actual publish write,
which is the write path `publication_gate`'s own docstring already noted
was still missing).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.audit_log import record_publish
from backend.domain.publication_gate import (
    PublishBlocked,
    attempt_publish,
    check_open_conflict,
    check_volume_completeness,
)
from backend.models.entities import Batch, Record

__all__ = ["PublishBlocked", "latest_version", "publish_new_version"]


def latest_version(session: Session, record_group_id: str) -> Record | None:
    return session.execute(
        select(Record)
        .where(Record.record_group_id == record_group_id)
        .order_by(Record.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def publish_new_version(
    session: Session,
    *,
    record_group_id: str | None,
    batch_id: str | None,
    actor: str,
    parcel_ref: str | None = None,
    ulpin: str | None = None,
    lgd_codes: dict | None = None,
) -> Record:
    """`record_group_id=None` starts a new logical record (its first
    published version gets a fresh `record_group_id`, defaulted by the
    column itself); passing an existing group id publishes the next
    version of that record. Runs the gate (batch completeness +
    conflict-on-the-*previous* version, since a not-yet-created version
    has no conflict of its own yet) before writing anything — raises
    `PublishBlocked` (never a partial write) if either check fails.
    """
    checks = []
    if batch_id is not None:
        checks.append(lambda s, subj: check_volume_completeness(s, batch_id))
    previous = latest_version(session, record_group_id) if record_group_id else None
    if previous is not None:
        checks.append(lambda s, subj: check_open_conflict(s, previous.id))

    subject_id = record_group_id or batch_id or "new-record"
    attempt_publish(session, subject_id, checks)  # raises PublishBlocked, never partially writes below

    next_version = (previous.version + 1) if previous else 1
    record = Record(
        record_group_id=record_group_id,  # None -> column default fills a fresh id
        version=next_version,
        parcel_ref=parcel_ref if parcel_ref is not None else (previous.parcel_ref if previous else None),
        ulpin=ulpin if ulpin is not None else (previous.ulpin if previous else None),
        lgd_codes=lgd_codes if lgd_codes is not None else (previous.lgd_codes if previous else None),
        status="published",
        published_at=datetime.now(timezone.utc),
    )
    session.add(record)
    session.flush()

    district = None
    if batch_id is not None:
        batch = session.get(Batch, batch_id)
        district = batch.district if batch else None
    record_publish(session, actor=actor, record_id=record.record_group_id, version=record.version, district=district)
    session.flush()
    return record
