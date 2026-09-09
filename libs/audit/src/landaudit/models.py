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
