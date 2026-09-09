"""ORM mapping for `outbox_message` (ADR-005). The table is created by
`infra/migrations` alongside every other table this monorepo shares one
Postgres schema and one migration history for (CLAUDE.md)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OutboxBase(DeclarativeBase):
    pass


class OutboxMessage(OutboxBase):
    __tablename__ = "outbox_message"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    queue: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # The full envelope shape from observability.envelope.emit() —
    # {message_id, trace_id, emitted_at, producer, work_envelope, payload}.
    envelope: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    dispatched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
