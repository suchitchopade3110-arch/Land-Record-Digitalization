"""Phase 3 (adjudication band) schema additions.

Three new backend-owned tables — none of them `contracts/schemas/*.json`
entities, so none of this touches `contracts/` (same reasoning as 0003's
Batch/RescanTask/FixityCheck, and PHASE3.md's "correction to the build
prompt" section):

1. `review_session` (P3-13/FR-REV-16) — the session anchor
   `ReviewTask.hour_into_session` needs. No other team reads/writes it.
2. `pending_correction` (P3-07/FR-REV-12) — maker-checker staging. Kept
   structurally separate from `correction` (frozen contract table) so an
   unconfirmed edit is absent from Tharun's training-store read contract
   by construction, not by a status filter every reader has to remember.
3. `operational_alert` (P3-02/FR-CNF-14) — one row per novelty cluster
   alert, deduplicated within a config window; not a `ReviewTask`.

Plus a partial index backing `review_workflow.claim_next`'s
`SELECT ... FOR UPDATE SKIP LOCKED` predicate (P3-03: "index the claim
predicate, no N+1").

Uses `BackendBase.metadata.create_all(bind, tables=[...])`, the same
pattern 0002 uses for the rest of `entities.py` — read straight from the
live ORM model so column types/constraints never drift from what
`backend.models.entities` declares.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09 00:00:02
"""
from alembic import op

from backend.models.base import Base as BackendBase
from backend.models.entities import OperationalAlert, PendingCorrection, ReviewSession

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    BackendBase.metadata.create_all(
        bind,
        tables=[
            ReviewSession.__table__,
            PendingCorrection.__table__,
            OperationalAlert.__table__,
        ],
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_review_task_claimable "
        "ON review_task (opened_at) WHERE assignee IS NULL AND closed_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_review_task_claimable")
    op.drop_table("operational_alert")
    op.drop_table("pending_correction")
    op.drop_table("review_session")
