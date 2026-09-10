"""Phase 5 — `closed_set_entry` (P5-05, contract §4.3, FR-VAL-09).

One new backend-owned table, not a `contracts/schemas/*.json` entity (same
reasoning as 0003's Batch/RescanTask/FixityCheck and 0004's
review_session/pending_correction/operational_alert): backend-internal
storage behind the `/closed-sets/{type}` synchronous read API.

`closed_set_entry.config_version` is a real `ForeignKey` to
`config_version.id` (declared directly on the column in
`backend.models.entities.ClosedSetEntry`) — a closed-set corpus batch can
never point at a `config_version` id that doesn't exist, same posture as
every other FK in this schema.

Uses `BackendBase.metadata.create_all(bind, tables=[...])`, the same
pattern 0004 uses — read straight from the live ORM model so column
types/constraints never drift from what `backend.models.entities`
declares.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09 00:00:05
"""
from alembic import op

from backend.models.base import Base as BackendBase
from backend.models.entities import ClosedSetEntry

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    BackendBase.metadata.create_all(bind, tables=[ClosedSetEntry.__table__])


def downgrade() -> None:
    op.drop_table("closed_set_entry")
