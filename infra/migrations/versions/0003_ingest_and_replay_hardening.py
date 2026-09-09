"""Phase 2 (acquisition band) schema hardening.

Three additive changes, none touching `contracts/` (Batch/RescanTask/
FixityCheck have no `contracts/schemas/*.json` counterpart — they're
backend-owned tables, not frozen cross-team contracts):

1. `work_envelope.page_id` gets a real UNIQUE constraint — the DB-level
   backing for FR-TRI-09's "pin once per page" guarantee
   (`landenvelope.pin.pin` already does a get-or-create check in Python;
   this is belt-and-suspenders against a caller that bypasses it, matching
   every other invariant in this repo being enforced at the DB layer, not
   just by convention). T2.d (replay determinism) depends on this holding
   even under a race, not just in the common sequential case.
2. `rescan_task.state` gets the CHECK constraint `backend.models.entities`
   already declares — added here for a pre-existing table; a fresh
   `alembic upgrade head` run gets it straight from 0002's `create_all`
   (which reads the live model), so both guarded with an existence check
   below to make this migration safe to run either way.
3. `batch.scanning_date`, `page.storage_uri`, and `fixity_check.store` —
   new nullable-with-default columns (FR-ING-05 batch metadata; FR-ING-03
   per-page object-store key; FR-ING-07 dual-copy fixity), added with
   `IF NOT EXISTS` for the same fresh-vs-upgrade reason.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-09 00:00:01
"""
from alembic import op
from sqlalchemy import text

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _constraint_exists(bind, name: str) -> bool:
    return bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": name}).scalar() is not None


def upgrade() -> None:
    bind = op.get_bind()

    if not _constraint_exists(bind, "uq_work_envelope_page_id"):
        op.create_unique_constraint("uq_work_envelope_page_id", "work_envelope", ["page_id"])

    if not _constraint_exists(bind, "ck_rescan_task_state_enum"):
        op.create_check_constraint("ck_rescan_task_state_enum", "rescan_task", "state IN ('open','assigned','closed')")

    op.execute("ALTER TABLE batch ADD COLUMN IF NOT EXISTS scanning_date TIMESTAMPTZ")
    op.execute("ALTER TABLE page ADD COLUMN IF NOT EXISTS storage_uri VARCHAR")
    op.execute("ALTER TABLE fixity_check ADD COLUMN IF NOT EXISTS store VARCHAR NOT NULL DEFAULT 'primary'")

    if not _constraint_exists(bind, "ck_fixity_check_store_enum"):
        op.create_check_constraint("ck_fixity_check_store_enum", "fixity_check", "store IN ('primary','secondary')")


def downgrade() -> None:
    op.execute("ALTER TABLE fixity_check DROP CONSTRAINT IF EXISTS ck_fixity_check_store_enum")
    op.execute("ALTER TABLE fixity_check DROP COLUMN IF EXISTS store")
    op.execute("ALTER TABLE page DROP COLUMN IF EXISTS storage_uri")
    op.execute("ALTER TABLE batch DROP COLUMN IF EXISTS scanning_date")
    op.execute("ALTER TABLE rescan_task DROP CONSTRAINT IF EXISTS ck_rescan_task_state_enum")
    op.execute("ALTER TABLE work_envelope DROP CONSTRAINT IF EXISTS uq_work_envelope_page_id")
