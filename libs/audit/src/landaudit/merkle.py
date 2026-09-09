"""FR-PUB-09 — the periodic global root: a Merkle tree over shard heads,
not over individual entries. Rolled on a configured interval (P4-04,
default hourly — see `backend.domain.publication_policy.ROOT_ROLL_INTERVAL`
and PHASE4.md's "root roll interval" section).

Committing to shard *heads* rather than every entry keeps the root cheap
to recompute on every roll (`NUM_SHARDS` hashes, not
`NUM_SHARDS * entries_per_shard`) while still making the root a function
of the entire chain's state at roll time: a shard head is itself the
result of that shard's whole hash chain up to that point, so a tampered
entry anywhere in a shard's history changes that shard's current head
(`ChainTamperedError` on `verify_shard`, `chain.py`), which changes the
root.

The tree records which shard heads it committed to (`ShardHeadRecord`)
so a verifier can reconstruct the exact tree from the stored `chain_root`
row alone — recomputing what the root *should* be from the shard heads it
names, not from a black-box "trust the stored root" claim.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from landaudit.chain import NUM_SHARDS
from landaudit.models import AuditEntry


@dataclass(frozen=True)
class ShardHeadRecord:
    shard_id: int
    head_hash: str | None  # None => shard has no entries yet


def _leaf_hash(shard_id: int, head_hash: str | None) -> str:
    return hashlib.sha256(f"{shard_id}:{head_hash or ''}".encode()).hexdigest()


def _pair_hash(left: str, right: str) -> str:
    return hashlib.sha256((left + right).encode()).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    """Standard binary Merkle root over an ordered leaf list. An odd
    level duplicates its last node (widely-used, simple convention —
    documented here since it's a real choice a verifier must reproduce
    identically)."""
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [_pair_hash(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def current_shard_heads(session: Session) -> list[ShardHeadRecord]:
    """Walk every shard in a fixed order (0..NUM_SHARDS-1) and read its
    latest hash. Fixed order and an explicit `None` for an empty shard
    (never an omission) so the set of leaves the tree commits to never
    silently shifts as shards fill in over time — a verifier reconstructs
    the same tree from the same `NUM_SHARDS`-length list regardless of
    which shards happened to have entries at roll time."""
    heads = []
    for shard_id in range(NUM_SHARDS):
        latest = session.execute(
            select(AuditEntry.hash)
            .where(AuditEntry.shard_id == shard_id)
            .order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        heads.append(ShardHeadRecord(shard_id=shard_id, head_hash=latest))
    return heads


def compute_root(heads: list[ShardHeadRecord]) -> str:
    """The root a verifier recomputes from a stored `chain_root` row's own
    `shard_heads` column — no trust placed in the stored root value
    itself, only in the ability to recompute it from named inputs."""
    ordered = sorted(heads, key=lambda h: h.shard_id)
    leaves = [_leaf_hash(h.shard_id, h.head_hash) for h in ordered]
    return merkle_root(leaves)


def roll_global_root(session: Session) -> tuple[str, list[ShardHeadRecord]]:
    """FR-PUB-09 — compute this roll's root and the shard heads it commits
    to. Does not persist anything itself (see `rollup.perform_roll`, which
    writes the `chain_root` row and calls the witness) — kept pure so a
    verifier can call exactly this function against its own untrusted read
    of the DB and compare."""
    heads = current_shard_heads(session)
    return compute_root(heads), heads
