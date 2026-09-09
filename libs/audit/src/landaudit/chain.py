"""FR-PUB-03/09 — append-only, hash-chained, sharded audit log.

Each shard maintains its own hash chain (`prev_hash` chains only within a
shard, per ADR-006); a periodic rollup (`rollup.py`) combines all shards'
latest hashes into one global root, which is what `anchor.py` externally
anchors (FR-PUB-08).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from landaudit.models import AuditEntry

NUM_SHARDS = 8  # ADR-006 — enough to remove serial contention at pilot scale


def shard_for(key: str) -> int:
    """Deterministic shard assignment. `key` is conventionally `subject`
    (e.g. a record or extraction id) so that all audit history for one
    subject lands in one shard and can be walked without cross-shard
    reads — pass `actor` only when there's no natural subject (e.g. a
    login event)."""
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % NUM_SHARDS


def _compute_hash(prev_hash: str | None, actor: str, action: str, subject: str | None,
                   purpose: str | None, value_hash: str | None, at_iso: str) -> str:
    material = "|".join([prev_hash or "", actor, action, subject or "", purpose or "", value_hash or "", at_iso])
    return hashlib.sha256(material.encode()).hexdigest()


def append(
    session: Session,
    *,
    actor: str,
    action: str,
    subject: str | None = None,
    purpose: str | None = None,
    value_hash: str | None = None,
    shard_id: int | None = None,
) -> AuditEntry:
    """Append one entry to the chain. `value_hash` — never the value
    itself (FR-SEC-08); callers hash a value before calling this, they
    never pass the value in. Does not commit — the caller commits, in the
    same transaction as whatever domain action this entry records, so the
    audit trail and the action it describes are atomic with each other.

    Locks the shard's most recent row (`FOR UPDATE`) before computing the
    next hash, so two concurrent writers to the same shard don't both
    compute a hash chained off the same `prev_hash`.
    """
    resolved_shard = shard_id if shard_id is not None else shard_for(subject or actor)

    last = session.execute(
        select(AuditEntry)
        .where(AuditEntry.shard_id == resolved_shard)
        .order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()

    entry = AuditEntry(
        shard_id=resolved_shard,
        prev_hash=last.hash if last else None,
        actor=actor,
        action=action,
        subject=subject,
        purpose=purpose,
        value_hash=value_hash,
        # Set explicitly (rather than relying on the column's Python-side
        # default, which only fires at flush) so the timestamp used in
        # the hash computation below is exactly the one that gets stored.
        at=datetime.now(timezone.utc),
    )
    entry.hash = _compute_hash(
        entry.prev_hash, actor, action, subject, purpose, value_hash, entry.at.isoformat()
    )
    session.add(entry)
    session.flush()
    return entry


class ChainTamperedError(Exception):
    """Raised by `verify_shard` (in strict mode) or returned as a finding
    by `verify_shard(..., raise_on_mismatch=False)`. Names the first entry
    where the recorded hash no longer matches what recomputing the chain
    from that entry's own fields would produce."""


def verify_shard(session: Session, shard_id: int, *, raise_on_mismatch: bool = True) -> bool:
    """Walk one shard's entries in chain order and recompute every hash.
    Returns True if the shard verifies end to end. A modified log entry —
    even one where the attacker also recomputed *that* entry's own
    `hash` field to look self-consistent — is caught because the *next*
    entry's `prev_hash` no longer matches, and that next entry's own hash
    was computed over the true prior hash, not the tampered one."""
    entries = session.execute(
        select(AuditEntry).where(AuditEntry.shard_id == shard_id).order_by(AuditEntry.at.asc(), AuditEntry.id.asc())
    ).scalars().all()

    prev_hash: str | None = None
    for entry in entries:
        expected = _compute_hash(
            prev_hash, entry.actor, entry.action, entry.subject, entry.purpose, entry.value_hash,
            entry.at.isoformat(),
        )
        if entry.prev_hash != prev_hash or entry.hash != expected:
            if raise_on_mismatch:
                raise ChainTamperedError(f"shard {shard_id} entry {entry.id} does not chain from its predecessor")
            return False
        prev_hash = entry.hash
    return True
