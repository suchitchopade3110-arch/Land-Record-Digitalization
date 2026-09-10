"""P5-06/FR-ANL-01 — add `audit_entry.district`, plain metadata (not part
of the hash chain — see `landaudit.chain.append`'s docstring for why).

Every district-aware audit call already threads a district through to
compute a shard key (`landaudit.chain.shard_key_for`); until this column
existed, that value was discarded after hashing into a shard index —
nothing preserved the literal district string anywhere queryable. The
dashboard's "pages ingested/processed/published, by district" figures
(FR-ANL-01) need a `GROUP BY` over exactly that.

`ALTER TABLE ... ADD COLUMN` on a `PARTITION BY HASH` parent (ADR-006)
propagates to every child partition automatically — no per-partition DDL
needed, unlike 0002's partition-creation step.

Guarded like 0006's own idempotency check: `0002_core_schema.py` builds
`audit_entry` via `AuditBase.metadata.create_all(bind)`, which reads the
*live* `AuditEntry` model — so on a database migrated from scratch after
this column was added to that model, 0002 already creates it, and this
migration is a genuine no-op. It still matters for a database that had
already run 0001-0008 before this revision existed and only later
upgrades to head, which is the actual "add a column" case this migration
exists for.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-10T00:00:00
"""
from alembic import op
from sqlalchemy import Column, String, inspect

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "audit_entry", "district"):
        op.add_column("audit_entry", Column("district", String(), nullable=True))
    if not any(
        ix["name"] == "ix_audit_entry_district" for ix in inspect(bind).get_indexes("audit_entry")
    ):
        op.create_index("ix_audit_entry_district", "audit_entry", ["district"])


def downgrade() -> None:
    op.drop_index("ix_audit_entry_district", table_name="audit_entry")
    op.drop_column("audit_entry", "district")
