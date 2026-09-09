"""FR-PUB-08 — chain roots anchored to a witness outside this
application's administrative control. **Counterparty is not decided**
(PRD §11 Q8 — see docs/open-questions.md); this module defines the
interface the decision plugs into, plus a P0 stand-in
(`LocalSecondStoreWitness`: a daily signed root written to a second store
under different credentials) that is real as a mechanism but is not the
externally-anchored witness the requirement ultimately needs. Do not name
a specific government-timestamping integration here until Q8 is answered
— see CLAUDE.md's "stop and ask" list.
"""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from landaudit.chain import NUM_SHARDS
from landaudit.models import AuditEntry


def compute_global_root(session: Session) -> str:
    """Roll every shard's latest hash into one global root
    (FR-PUB-09) — the value that gets anchored. Deterministic: shards are
    walked in a fixed order (0..NUM_SHARDS-1); a shard with no entries
    yet contributes an empty string, not an omission, so the root's
    definition doesn't shift as shards fill up."""
    latest_hashes = []
    for shard_id in range(NUM_SHARDS):
        latest = session.execute(
            select(AuditEntry.hash)
            .where(AuditEntry.shard_id == shard_id)
            .order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        latest_hashes.append(latest or "")
    return hashlib.sha256("|".join(latest_hashes).encode()).hexdigest()


@dataclass(frozen=True)
class AnchorReceipt:
    root: str
    anchored_at: str
    witness: str
    witness_reference: str  # e.g. a second store's object key, or (once Q8 resolves) a TSA token


class AnchorWitness(abc.ABC):
    """A witness outside this application's administrative control. A
    real implementation must not be reachable with the same credentials
    that can write to this application's own Postgres — otherwise it
    anchors nothing against the threat FR-PUB-08 names (an insider with
    this application's DB write access)."""

    @abc.abstractmethod
    def anchor(self, root: str) -> AnchorReceipt: ...


class LocalSecondStoreWitness(AnchorWitness):
    """P0 stand-in: writes a daily signed root to a second local store.
    The *mechanism* (root computed once, written somewhere the primary
    write path doesn't touch, receipt returned and itself auditable) is
    real; the *credential separation and off-infrastructure placement*
    that make it a genuine external witness are not — do not present this
    class as satisfying FR-PUB-08 in a pilot or production context."""

    def __init__(self, store):
        # `store` is any `landstorage.ObjectStorePort` — content-addressed,
        # so the receipt for a given root is itself dedupable and immutable.
        self._store = store

    def anchor(self, root: str) -> AnchorReceipt:
        anchored_at = datetime.now(timezone.utc).isoformat()
        payload = f"{root}|{anchored_at}".encode()
        result = self._store.put(payload)
        return AnchorReceipt(
            root=root, anchored_at=anchored_at, witness="local_second_store", witness_reference=result.key
        )
