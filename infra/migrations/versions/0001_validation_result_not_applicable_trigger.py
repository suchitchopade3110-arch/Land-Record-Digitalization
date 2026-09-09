"""FR-VAL-09 — consumed_constraints non-empty forces verdict = not_applicable.

SUPERSEDED by 0002_core_schema.py: the invariant is enforced there as a
Postgres CHECK constraint on `validation_result`
(`ck_validation_result_consumed_constraints_forces_not_applicable`) rather
than the BEFORE INSERT/UPDATE trigger this file sketches — a CHECK
constraint is what CLAUDE.md's invariant list and the original brief both
actually asked for, and it's simpler than a trigger function for the same
guarantee. Left in place, still a no-op (upgrade()/downgrade() below are
unchanged), as a historical record rather than rewritten — 0002 is the
migration that actually creates `validation_result` and everything else.

Revision ID: 0001
Revises:
Create Date: 2026-01-01 00:00:00
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# TODO: replace `validation_result` below once the table's real DDL exists —
# this trigger assumes columns `verdict text` and `consumed_constraints text[]`.
_CREATE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION enforce_validation_result_not_applicable()
RETURNS trigger AS $$
BEGIN
    IF array_length(NEW.consumed_constraints, 1) > 0 AND NEW.verdict <> 'not_applicable' THEN
        RAISE EXCEPTION
            'FR-VAL-09 violation: consumed_constraints is non-empty but verdict is %, must be not_applicable',
            NEW.verdict;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_CREATE_TRIGGER = """
CREATE TRIGGER validation_result_not_applicable_trigger
BEFORE INSERT OR UPDATE ON validation_result
FOR EACH ROW EXECUTE FUNCTION enforce_validation_result_not_applicable();
"""


def upgrade() -> None:
    # TODO: uncomment once the validation_result table exists.
    # op.execute(_CREATE_TRIGGER_FN)
    # op.execute(_CREATE_TRIGGER)
    pass


def downgrade() -> None:
    # op.execute("DROP TRIGGER IF EXISTS validation_result_not_applicable_trigger ON validation_result;")
    # op.execute("DROP FUNCTION IF EXISTS enforce_validation_result_not_applicable;")
    pass
