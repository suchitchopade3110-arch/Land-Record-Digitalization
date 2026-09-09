"""Core schema — every table in the Phase 1 gate: WorkEnvelope,
outbox_message, audit_entry (hash-partitioned), and services/backend's
domain tables (Batch through LegacyRecordRef). Supersedes 0001's
trigger-based approach to FR-VAL-09 — that invariant is enforced here as a
Postgres CHECK constraint on `validation_result` instead (see
`ck_validation_result_consumed_constraints_forces_not_applicable` in
`backend.models.entities.ValidationResult.__table_args__`), which is what
CLAUDE.md's invariant list actually calls for. 0001 is left in place,
disabled, as a historical record rather than rewritten.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09 00:00:00
"""
from alembic import op

from backend.models.base import Base as BackendBase
from landaudit.models import AuditBase
from landenvelope.models import EnvelopeBase
from landoutbox.models import OutboxBase

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NUM_SHARDS = 8  # ADR-006, must match landaudit.chain.NUM_SHARDS

_WORK_ENVELOPE_IMMUTABLE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION reject_work_envelope_update()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'work_envelope is immutable after write (FR-TRI-09) — envelope_id=% cannot be updated',
        OLD.envelope_id;
END;
$$ LANGUAGE plpgsql;
"""

_WORK_ENVELOPE_IMMUTABLE_TRIGGER = """
CREATE TRIGGER work_envelope_reject_update
BEFORE UPDATE ON work_envelope
FOR EACH ROW EXECUTE FUNCTION reject_work_envelope_update();
"""


def upgrade() -> None:
    bind = op.get_bind()

    # Shared-mechanism tables first (no FK dependency on backend's domain
    # tables), then backend's own domain schema.
    EnvelopeBase.metadata.create_all(bind)
    OutboxBase.metadata.create_all(bind)
    # AuditEntry is declared PARTITION BY HASH (shard_id) — create_all()
    # emits the parent table with no storage of its own; the 8 child
    # partitions are created explicitly right after.
    AuditBase.metadata.create_all(bind)
    BackendBase.metadata.create_all(bind)

    for shard in range(NUM_SHARDS):
        op.execute(
            f"""
            CREATE TABLE audit_entry_p{shard}
            PARTITION OF audit_entry
            FOR VALUES WITH (MODULUS {NUM_SHARDS}, REMAINDER {shard});
            """
        )

    # Invariant 3 (CLAUDE.md) / FR-TRI-09: WorkEnvelope immutable after
    # write. `landenvelope.pin` exposes no update() method at the Python
    # level; this is the storage-layer guarantee that holds even if
    # something bypasses the Python API and issues raw SQL.
    op.execute(_WORK_ENVELOPE_IMMUTABLE_TRIGGER_FN)
    op.execute(_WORK_ENVELOPE_IMMUTABLE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS work_envelope_reject_update ON work_envelope;")
    op.execute("DROP FUNCTION IF EXISTS reject_work_envelope_update;")

    bind = op.get_bind()
    # Reverse creation order; audit_entry's partitions drop automatically
    # with the parent (ON DELETE CASCADE is implicit for partition DDL).
    BackendBase.metadata.drop_all(bind)
    AuditBase.metadata.drop_all(bind)
    OutboxBase.metadata.drop_all(bind)
    EnvelopeBase.metadata.drop_all(bind)
