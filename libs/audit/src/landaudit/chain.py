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

# P4-03/FR-PUB-09 — the shard-key strategy, decided and written down before
# implementation (per the build prompt's own instruction, since retrofitting
# a shard key means rewriting hashes that are supposed to be immutable).
# See PHASE4.md's "shard-key strategy" section for the full rationale.
#
# `SYSTEM_SHARD_ID` is a *reserved* shard index, not just "whatever
# subject/actor happens to hash to" — it is the defined fallback for entry
# types with no natural district: config changes, role grants, login
# events, anchor writes, reprocessing jobs. A caller asks for it explicitly
# by passing `shard_key=SYSTEM_SHARD_KEY` (never falls into it by hashing).
SYSTEM_SHARD_ID = 0
SYSTEM_SHARD_KEY = "system"


def shard_for(key: str) -> int:
    """Deterministic shard assignment from a stable key. `key=SYSTEM_SHARD_KEY`
    always resolves to the reserved system shard; any other key hashes into
    the full `0..NUM_SHARDS-1` range (so a hash collision landing on shard 0
    is expected and harmless — the system shard is reserved *by convention
    of which keys route there*, not by carving out a dedicated numeric
    range no other key may ever hash into).

    Pre-Phase-4 callers passed `subject` (an extraction/task/conflict id)
    as this key, which has no natural district — see PHASE4.md's
    "shard-key strategy" section for why those call sites were not
    retroactively rewired to a district key this phase (a real, named
    gap, not a silent one)."""
    if key == SYSTEM_SHARD_KEY:
        return SYSTEM_SHARD_ID
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % NUM_SHARDS


def shard_key_for(*, district: str | None = None, batch_id: str | None = None) -> str:
    """FR-PUB-03/09 — the one place a caller turns "what district/batch is
    this entry about" into the key `shard_for`/`append(shard_key=...)`
    consumes. `district` wins when both are known (it's the more stable,
    more human-legible grouping); `batch_id` is the fallback for an entry
    type that knows its batch but not yet its district; `SYSTEM_SHARD_KEY`
    is the fallback of last resort — always explicit, never a silent
    default from an empty string hashing somewhere arbitrary."""
    if district:
        return f"district:{district}"
    if batch_id:
        return f"batch:{batch_id}"
    return SYSTEM_SHARD_KEY


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
    shard_key: str | None = None,
    district: str | None = None,
) -> AuditEntry:
    """Append one entry to the chain. `value_hash` — never the value
    itself (FR-SEC-08); callers hash a value before calling this, they
    never pass the value in (see `landaudit.valuehash` for the keyed HMAC
    that must produce it — a bare SHA-256 of a small-domain value like a
    name or survey number is dictionary-attackable). Does not commit — the
    caller commits, in the same transaction as whatever domain action this
    entry records, so the audit trail and the action it describes are
    atomic with each other.

    `district` (P5-06/FR-ANL-01) is stored as plain metadata, queryable
    directly (`AuditEntry.district`, indexed) — it is deliberately NOT
    part of `_compute_hash`'s material. Including it would mean every
    entry ever written before this parameter existed was hashed under a
    different formula, so verifying old and new rows the same way would
    require either two formulas or a full historical rehash — a real
    change to the audit chain's tamper-evidence guarantee (T4.a) that a
    dashboard-aggregation feature should not make as a side effect. If
    `district` ever needs the same tamper-evidence guarantee actor/
    action/subject/purpose/value_hash/at already have, that is a
    deliberate ADR-level decision about the hash chain itself, not an
    incidental one.

    Shard resolution, in priority order: an explicit `shard_id` (rare —
    only when a caller already knows the exact partition, e.g. the
    verifier); `shard_key` (P4-03's strategy — `landaudit.chain.shard_key_for`
    turns a district/batch_id into this); falling back, for callers written
    before P4-03, to hashing `subject or actor` directly (see `shard_for`'s
    docstring for why this fallback exists and its known limitation).

    Locks the shard's most recent row (`FOR UPDATE`) before computing the
    next hash, so two concurrent writers to the same shard don't both
    compute a hash chained off the same `prev_hash`.
    """
    if shard_id is not None:
        resolved_shard = shard_id
    elif shard_key is not None:
        resolved_shard = shard_for(shard_key)
    else:
        resolved_shard = shard_for(subject or actor)

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
        district=district,
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
