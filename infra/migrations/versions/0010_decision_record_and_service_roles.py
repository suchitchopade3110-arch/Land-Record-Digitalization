"""T1-03 / D1-A / D2 — Create decision_record table for replay idempotency
and establish service DB roles with column-level grants for ownership boundary.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-11 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return table in inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Create decision_record table if not exists (D2 replay idempotency)
    if not _table_exists(bind, "decision_record"):
        op.create_table(
            "decision_record",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("extraction_id", sa.String(), nullable=False, unique=True),
            sa.Column("outcome", sa.String(), nullable=False),
            sa.Column("envelope_id", sa.String(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_decision_record_extraction_id",
            "decision_record",
            ["extraction_id"],
            unique=True,
        )

    # 2. On Postgres, create service roles and column-level GRANTs (D1-A)
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'modelwork_role') THEN
                    CREATE ROLE modelwork_role;
                END IF;
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'extraction_role') THEN
                    CREATE ROLE extraction_role;
                END IF;
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'validation_role') THEN
                    CREATE ROLE validation_role;
                END IF;
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'backend_role') THEN
                    CREATE ROLE backend_role;
                END IF;
            END $$;

            -- D1-A Column-level privileges:
            GRANT SELECT ON extractions TO modelwork_role;
            GRANT UPDATE (routing_outcome, calibrated_confidence) ON extractions TO modelwork_role;

            GRANT SELECT, INSERT ON extractions TO extraction_role;
            GRANT UPDATE (raw_text, canonical_name, bounding_box, status) ON extractions TO extraction_role;

            GRANT SELECT ON extractions TO backend_role;
            GRANT SELECT, INSERT, UPDATE, DELETE ON decision_record TO backend_role;
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            REVOKE ALL PRIVILEGES ON decision_record FROM backend_role;
            REVOKE ALL PRIVILEGES ON extractions FROM backend_role;
            REVOKE ALL PRIVILEGES ON extractions FROM extraction_role;
            REVOKE ALL PRIVILEGES ON extractions FROM modelwork_role;
            """
        )

    if _table_exists(bind, "decision_record"):
        op.drop_table("decision_record")
