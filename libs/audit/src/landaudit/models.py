"""ORM mapping for `audit_entry` — hash-partitioned by `shard_id`
(ADR-006). The physical `PARTITION BY HASH (shard_id)` DDL is authored
directly in `infra/migrations/versions/0002_core_schema.py`; this is the
logical row shape. `value_hash`, never the value itself (FR-SEC-08) —
nothing in this package's writer accepts an unmasked value as an argument
that could be persisted; see `writer.py`."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AuditBase(DeclarativeBase):
    pass


class AuditEntry(AuditBase):
    __tablename__ = "audit_entry"
    # ADR-006: hash-partitioned so the chain never serializes on one
    # physical relation. SQLAlchemy emits the parent `PARTITION BY HASH
    # (shard_id)` DDL from this table arg; the 8 child partitions
    # themselves are created by raw SQL in the migration (Core has no
    # declarative construct for partition children).
    __table_args__ = ({"postgresql_partition_by": "HASH (shard_id)"},)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    # Part of the primary key so Postgres HASH partitioning by shard_id
    # (which requires the partition key in every unique/primary index) is
    # satisfiable.
    shard_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    prev_hash: Mapped[str | None] = mapped_column(String)
    hash: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    subject: Mapped[str | None] = mapped_column(String)
    purpose: Mapped[str | None] = mapped_column(String)  # required for an unmasked-read action, FR-SEC-08
    value_hash: Mapped[str | None] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # P5-06/FR-ANL-01 — plain metadata, deliberately NOT part of the hash
    # chain's material (`chain._compute_hash` is unchanged by this
    # column's addition). Every district-aware audit call already threads
    # a district through to compute a shard key (`shard_key_for`), but
    # until this column existed that value was thrown away after hashing
    # into a shard index — nothing preserved the literal district string
    # anywhere queryable, which is exactly what "pages ingested/processed/
    # published, by district" (FR-ANL-01) needs a GROUP BY over. Adding it
    # to the hash material instead would mean recomputing/reverifying
    # every row ever written under the old formula, a real T4.a-relevant
    # change to the audit chain's cryptographic guarantee that a
    # dashboard-aggregation task should not make as a side effect — see
    # `chain.append`'s docstring for the same note at the write site.
    district: Mapped[str | None] = mapped_column(String, index=True)


class ChainRoot(AuditBase):
    """P4-04/FR-PUB-09 — one row per periodic roll. `shard_heads` names
    exactly which `(shard_id, head_hash)` pairs the Merkle tree committed
    to (`landaudit.merkle.ShardHeadRecord`, serialized), so a verifier can
    reconstruct the tree from this row alone — `landaudit.merkle.compute_root`
    over `shard_heads` must reproduce `root` exactly, or this row's own
    claim about itself is already inconsistent, independent of whether
    anything downstream was tampered with."""

    __tablename__ = "chain_root"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    root: Mapped[str] = mapped_column(String, nullable=False, index=True)
    rolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )
    shard_heads: Mapped[list] = mapped_column(JSONB, nullable=False)  # [{"shard_id": int, "head_hash": str|None}]
    config_version: Mapped[str | None] = mapped_column(String)  # which config pinned the roll interval, FR-CFG-02


class AnchorReceipt(AuditBase):
    """P4-05/FR-PUB-08 — the persisted record of one `Witness.anchor()`
    call. Distinct from `landaudit.witness.WitnessReceipt` (the in-memory
    dataclass a `Witness` implementation returns): this is the DB row that
    survives past the call that created it, which is what
    `verify.py`'s "earliest anchored root the chain disagrees with" needs
    to walk back through."""

    __tablename__ = "anchor_receipt"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    chain_root_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    root: Mapped[str] = mapped_column(String, nullable=False, index=True)
    anchored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    witness: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "second_store" | "timestamp_authority"
    witness_reference: Mapped[str] = mapped_column(String, nullable=False)  # second store's key / TSA token ref
    signature: Mapped[str | None] = mapped_column(String)  # KMS sign-only handle's signature over `root`
    kms_key_id: Mapped[str | None] = mapped_column(String)  # which sign-only key signed it, never the key itself
