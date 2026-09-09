"""ORM mapping for `work_envelope` — shape frozen by
`contracts/schemas/work_envelope.schema.json` / API-Contracts §2. The table
itself is created by `infra/migrations` (single migration history, per
CLAUDE.md); this model maps onto it. Immutability (invariant 3, CLAUDE.md)
is enforced at the DB level by a trigger the same migration installs — see
`infra/migrations/versions/0002_core_schema.py`. Nothing in this package
exposes an `update()`/`delete()` path, on top of that DB-level guarantee.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EnvelopeBase(DeclarativeBase):
    pass


class WorkEnvelope(EnvelopeBase):
    __tablename__ = "work_envelope"

    envelope_id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    page_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # {"triage_classifier": "vX", "printed_ocr": "vX", "hwr": "vX",
    #  "confidence_calibrator": "vX", "novelty_detector": "vX"}
    model_versions: Mapped[dict] = mapped_column(JSON, nullable=False)
    config_version: Mapped[str] = mapped_column(String, nullable=False)

    def to_contract_dict(self) -> dict:
        """The exact shape `work_envelope.schema.json` requires — what
        gets attached to every downstream queue message."""
        return {
            "envelope_id": self.envelope_id,
            "document_id": self.document_id,
            "page_id": self.page_id,
            "pinned_at": self.pinned_at.isoformat(),
            "model_versions": self.model_versions,
            "config_version": self.config_version,
        }
