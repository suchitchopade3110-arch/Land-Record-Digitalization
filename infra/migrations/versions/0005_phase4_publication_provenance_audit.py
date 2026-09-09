"""Phase 4 (publication/provenance/audit band) schema additions.

Five additive changes, none touching `contracts/`:

1. `record.record_group_id` — P4-01/FR-PUB-01's stable identity across
   versions (see `backend.models.entities.Record`'s docstring). Backfilled
   from `id` for any pre-existing row (this table has never had a real
   write before this migration in any environment this repo has run
   against, so the backfill is a no-op in practice, but it's here so this
   migration is honestly correct against a hypothetical non-empty table
   too), then made `NOT NULL`.
2. A trigger rejecting `UPDATE` on `record` outright — the same pattern
   0002's `work_envelope_reject_update` trigger uses for invariant 3.
   `uq_record_group_version` (unique on `(record_group_id, version)`)
   backs the "never the same version twice" half; the trigger backs the
   "never mutated in place" half.
3. `correction.created_at` — DB-only column (not in
   `contracts/schemas/correction.schema.json`, which is
   `additionalProperties: false` and frozen); backs P4-02's edit-history
   chronological ordering (`backend.domain.provenance.resolve_provenance`).
4. `field_provenance` — new backend-owned table (P4-02).
5. `chain_root` / `anchor_receipt` — new `landaudit`-owned tables (P4-04/
   P4-05), created the same way 0002 created `audit_entry`: read straight
   from `AuditBase.metadata`, not hand-authored DDL, so column
   types/constraints never drift from what `landaudit.models` declares.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-09 00:00:03
"""
from alembic import op
from sqlalchemy import text

from backend.models.base import Base as BackendBase
from backend.models.entities import FieldProvenance
from landaudit.models import AnchorReceipt, AuditBase, ChainRoot

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_RECORD_IMMUTABLE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION reject_record_update()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'record is append-only after publish (FR-PUB-01) — record_group_id=%, version=% cannot be updated; publish a new version instead',
        OLD.record_group_id, OLD.version;
END;
$$ LANGUAGE plpgsql;
"""

_RECORD_IMMUTABLE_TRIGGER = """
CREATE TRIGGER record_reject_update
BEFORE UPDATE ON record
FOR EACH ROW EXECUTE FUNCTION reject_record_update();
"""


def _constraint_exists(bind, name: str) -> bool:
    return bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": name}).scalar() is not None


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("ALTER TABLE record ADD COLUMN IF NOT EXISTS record_group_id VARCHAR")
    op.execute("UPDATE record SET record_group_id = id WHERE record_group_id IS NULL")
    op.execute("ALTER TABLE record ALTER COLUMN record_group_id SET NOT NULL")

    if not _constraint_exists(bind, "uq_record_group_version"):
        op.create_unique_constraint("uq_record_group_version", "record", ["record_group_id", "version"])

    # Invariant-shaped guarantee, P4-01: publish is append-only. Dropped
    # and recreated idempotently (mirrors 0002's phrasing) so this
    # migration is safe to re-run in a partially-applied state.
    op.execute("DROP TRIGGER IF EXISTS record_reject_update ON record;")
    op.execute(_RECORD_IMMUTABLE_TRIGGER_FN)
    op.execute(_RECORD_IMMUTABLE_TRIGGER)

    op.execute("ALTER TABLE correction ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()")

    BackendBase.metadata.create_all(bind, tables=[FieldProvenance.__table__])
    AuditBase.metadata.create_all(bind, tables=[ChainRoot.__table__, AnchorReceipt.__table__])


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS anchor_receipt")
    op.execute("DROP TABLE IF EXISTS chain_root")
    op.execute("DROP TABLE IF EXISTS field_provenance")
    op.execute("ALTER TABLE correction DROP COLUMN IF EXISTS created_at")
    op.execute("DROP TRIGGER IF EXISTS record_reject_update ON record;")
    op.execute("DROP FUNCTION IF EXISTS reject_record_update;")
    op.execute("ALTER TABLE record DROP CONSTRAINT IF EXISTS uq_record_group_version")
    op.execute("ALTER TABLE record DROP COLUMN IF EXISTS record_group_id")
