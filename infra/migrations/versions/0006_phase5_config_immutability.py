"""Phase 5 (M13 configuration management) — P5-01/P5-02.

Two additive changes to `config_version`, neither touching
`contracts/schemas/config_version.schema.json` (still frozen, still exactly
`id, scope, key, value, effective_from, author, approver, superseded_by`):

1. `uq_config_version_scope_key_effective_from` — `(scope, key,
   effective_from)` unique, per P5-01. Two versions of the same key cannot
   claim to take effect at the same instant; which one a reader would get
   would depend on undefined row order, and that ambiguity is exactly the
   kind of silent, district-wide error CLAUDE.md's config rules exist to
   prevent.
2. A trigger rejecting `UPDATE` on `config_version` for any column other
   than `superseded_by` — the same pattern 0002's
   `work_envelope_reject_update` and 0005's `record_reject_update` use.
   `author <> approver` (`ck_config_version_author_ne_approver`, FR-CFG-03)
   is already enforced as a CHECK constraint from Phase 1's schema
   creation (`backend.models.entities.ConfigVersion.__table_args__`); this
   migration does not touch it.

Not done here, and reported rather than silently skipped: the full
draft -> submitted -> approved -> effective -> superseded workflow state
machine P5-02 describes, and per-key JSONB schema validation on `value`
("a malformed unit table fails at write, not at read"). Both need a
workflow_state column and a per-key schema registry that don't exist yet
anywhere in this codebase; adding them is a real design decision (what
values `workflow_state` takes, how a per-key schema reference is looked
up) rather than a mechanical follow-on to this migration, so it is left
for a dedicated P5-02 pass rather than guessed at here.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-09 00:00:04
"""
from alembic import op
from sqlalchemy import text

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_CONFIG_VERSION_IMMUTABLE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION reject_config_version_update()
RETURNS trigger AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
        OR NEW.scope IS DISTINCT FROM OLD.scope
        OR NEW.key IS DISTINCT FROM OLD.key
        OR NEW.value IS DISTINCT FROM OLD.value
        OR NEW.effective_from IS DISTINCT FROM OLD.effective_from
        OR NEW.author IS DISTINCT FROM OLD.author
        OR NEW.approver IS DISTINCT FROM OLD.approver
    THEN
        RAISE EXCEPTION
            'config_version is immutable except for superseded_by (FR-CFG-01) — id=% cannot have its other columns updated',
            OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_CONFIG_VERSION_IMMUTABLE_TRIGGER = """
CREATE TRIGGER config_version_reject_update
BEFORE UPDATE ON config_version
FOR EACH ROW EXECUTE FUNCTION reject_config_version_update();
"""


def _constraint_exists(bind, name: str) -> bool:
    return (
        bind.execute(text("SELECT 1 FROM pg_constraint WHERE conname = :name"), {"name": name}).scalar()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _constraint_exists(bind, "uq_config_version_scope_key_effective_from"):
        op.create_unique_constraint(
            "uq_config_version_scope_key_effective_from",
            "config_version",
            ["scope", "key", "effective_from"],
        )

    # Dropped and recreated idempotently, same phrasing as 0002/0005, so
    # this migration is safe to re-run in a partially-applied state.
    op.execute("DROP TRIGGER IF EXISTS config_version_reject_update ON config_version;")
    op.execute(_CONFIG_VERSION_IMMUTABLE_TRIGGER_FN)
    op.execute(_CONFIG_VERSION_IMMUTABLE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS config_version_reject_update ON config_version;")
    op.execute("DROP FUNCTION IF EXISTS reject_config_version_update();")
    op.drop_constraint("uq_config_version_scope_key_effective_from", "config_version", type_="unique")
