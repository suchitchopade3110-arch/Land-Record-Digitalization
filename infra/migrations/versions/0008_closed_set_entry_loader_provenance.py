"""P5-05-fix — make `closed_set_entry.provenance` untamperable by callers
for the two *trusted* provenances (FR-VAL-09).

Finding this migration fixes: before this revision, `provenance` was
`NOT NULL` with only a `CHECK (provenance IN ('schema','lgd','lrms'))` —
i.e. any caller constructing a `ClosedSetEntry` row could set
`provenance='schema'` or `'lgd'` directly, same as setting `.source`. No
loader existed at all: `grep -rn "ClosedSetEntry(" services/backend/src`
before this fix turned up only the ORM class definition itself — nothing
in application code wrote these rows, so nothing enforced who was allowed
to claim a row was schema- or LGD-derived. That is a materially weaker
property than the corpus-partition rule needs: T5.c's mislabelled-row
test proved the *read* path ignores `.source` in favour of `.provenance`,
but said nothing about who gets to set `.provenance` in the first place.

This migration adds a trigger that requires a per-transaction Postgres
session setting (`SET LOCAL app.closed_set_loader_provenance = 'schema'`
or `'lgd'`) to already equal `NEW.provenance` before an INSERT of a
`'schema'`- or `'lgd'`-provenance row is allowed; anything else raises.
`backend.domain.closed_set_loaders` is the only code that ever issues
that `SET LOCAL` — `load_schema_derived_entries` sets it to `'schema'`,
`load_lgd_derived_entries` sets it to `'lgd'`, and neither function
accepts `provenance` as a parameter at all, so there is no calling
convention in Python that can produce a mismatched pair. `SET LOCAL` is
scoped to the current transaction only (Postgres resets it at COMMIT or
ROLLBACK), so it never leaks across pooled connections or between
requests.

`'lrms'`-provenance rows are deliberately left unrestricted by this
trigger — that value is already unconditionally excluded from every
`GET /closed-sets/{type}` response regardless of who wrote it (T5.c), so
there is nothing to protect by also gating it: a row that ends up
mislabelled `'lrms'` fails safe (invisible), never open.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-10T00:00:00
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION enforce_closed_set_entry_loader_provenance()
RETURNS trigger AS $$
DECLARE
    loader_provenance text;
BEGIN
    IF NEW.provenance IN ('schema', 'lgd') THEN
        loader_provenance := current_setting('app.closed_set_loader_provenance', true);
        IF loader_provenance IS DISTINCT FROM NEW.provenance THEN
            RAISE EXCEPTION
                'closed_set_entry.provenance=% must be written by its own sanctioned loader '
                '(app.closed_set_loader_provenance was %, expected %) — FR-VAL-09 corpus partition',
                NEW.provenance, coalesce(loader_provenance, '<unset>'), NEW.provenance;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER closed_set_entry_enforce_loader_provenance
BEFORE INSERT ON closed_set_entry
FOR EACH ROW EXECUTE FUNCTION enforce_closed_set_entry_loader_provenance();
"""


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS closed_set_entry_enforce_loader_provenance ON closed_set_entry;")
    op.execute(_TRIGGER_FN)
    op.execute(_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS closed_set_entry_enforce_loader_provenance ON closed_set_entry;")
    op.execute("DROP FUNCTION IF EXISTS enforce_closed_set_entry_loader_provenance();")
