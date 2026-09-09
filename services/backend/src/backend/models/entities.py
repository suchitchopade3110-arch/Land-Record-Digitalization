"""ORM mapping for services/backend's own domain tables — the full entity
list from PRD §06 plus the §3.12 "supporting entities" table in
API-Contracts-and-Interfaces.md, minus `WorkEnvelope` (`landenvelope`) and
`outbox_message` (`landoutbox`), which are shared-mechanism tables owned by
their own packages.

Column shapes are frozen by `contracts/schemas/*.json` where a schema
exists for the entity (`Extraction`, `ValidationResult`, `ReviewTask`,
`AuditSample`, `Correction`, `Conflict`, `ConfigVersion`, `ModelVersion`,
`ParcelGeometry`, `RecordAssembly`) — CLAUDE.md's frozen-contracts rule
applies to changing those columns, not just to `contracts/` files
themselves. `WorkEnvelope` (`landenvelope`), `outbox_message`
(`landoutbox`), and `audit_entry` (`landaudit`) are mapped in their own
packages, not here, because they're shared mechanisms every service can
write through, not backend-specific domain concepts — see each package's
`models.py`. Everything in this file is created by
`infra/migrations/versions/0002_core_schema.py`; this file is the ORM
mapping onto that DDL, kept in sync by hand.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Batch(Base):
    """FR-ING-05 batch metadata. `district` is the only mandatory field —
    rejected at ingest (`backend.domain.ingest.validate_batch_metadata`)
    rather than discovered missing at normalize, because unit conversion
    depends on it (P2-05)."""

    __tablename__ = "batch"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    district: Mapped[str] = mapped_column(String, nullable=False)  # mandatory — FR-ING-05
    tehsil: Mapped[str | None] = mapped_column(String)
    village: Mapped[str | None] = mapped_column(String)
    series: Mapped[str | None] = mapped_column(String)
    custodian: Mapped[str | None] = mapped_column(String)
    scanning_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # FR-ING-05
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SourceDocument(Base):
    __tablename__ = "source_document"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(String, ForeignKey("batch.id"), nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False, index=True)  # FR-ING-02/04
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)  # landstorage content-addressed key
    mime: Mapped[str] = mapped_column(String, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)


class Page(Base):
    __tablename__ = "page"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("source_document.id"), nullable=False, index=True)
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str | None] = mapped_column(String)  # FR-ING-03 — this page's own content-addressed key
    index_position: Mapped[int | None] = mapped_column(Integer)  # FR-ING-08 register's own page number
    quality_score: Mapped[float | None] = mapped_column(Float)  # *Tharun — FR-TRI-01
    legibility_band: Mapped[str | None] = mapped_column(String)  # good|marginal|poor — FR-TRI-10
    script: Mapped[str | None] = mapped_column(String)
    language: Mapped[str | None] = mapped_column(String)
    doc_type: Mapped[str | None] = mapped_column(String)  # FR-TRI-03
    page_role: Mapped[str | None] = mapped_column(String)  # FR-TRI-04
    writer_cluster_id: Mapped[str | None] = mapped_column(String, ForeignKey("writer_cluster.id"))
    novelty_score: Mapped[float | None] = mapped_column(Float)
    route: Mapped[list[str] | None] = mapped_column(ARRAY(String))  # ["text","map"] — FR-TRI-05

    __table_args__ = (
        CheckConstraint(
            "legibility_band IS NULL OR legibility_band IN ('good','marginal','poor')",
            name="ck_page_legibility_band_enum",
        ),
    )


class Extraction(Base):
    """Field-level and immutable — corrections create new versions rather
    than editing in place (PRD §06). `entry_status` invariant 2
    (CLAUDE.md): defaults to 'unknown', NEVER 'live', enforced below.
    """

    __tablename__ = "extraction"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    page_id: Mapped[str] = mapped_column(String, ForeignKey("page.id"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String, nullable=False)
    raw_value: Mapped[str] = mapped_column(String, nullable=False)  # never overwritten — FR-NRM-05
    canonical_value: Mapped[str | None] = mapped_column(String)
    unconstrained_value: Mapped[str | None] = mapped_column(String)  # FR-OCR-07 pre-gazetteer reading
    bbox: Mapped[dict | None] = mapped_column(JSONB)  # {x,y,w,h} — FR-OCR-04
    engine: Mapped[str | None] = mapped_column(String)  # printed_ocr|hwr|table_extractor
    model_version: Mapped[str | None] = mapped_column(String)
    config_version: Mapped[str | None] = mapped_column(String)
    token_confidence: Mapped[float | None] = mapped_column(Float)
    calibrated_confidence: Mapped[float | None] = mapped_column(Float)  # null until Tharun scores it
    novelty_score: Mapped[float | None] = mapped_column(Float)
    routing_outcome: Mapped[str | None] = mapped_column(String)
    entry_status: Mapped[str] = mapped_column(String, nullable=False, server_default="unknown")
    attestation_refs: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    top_k: Mapped[list | None] = mapped_column(JSONB)  # [{"value","confidence"}] — FR-OCR-06

    __table_args__ = (
        CheckConstraint(
            "entry_status IN ('unknown','live','cancelled','superseded','amended')",
            name="ck_extraction_entry_status_enum",
        ),
        CheckConstraint(
            "routing_outcome IS NULL OR routing_outcome IN "
            "('auto_accept','audit_sample','review','conflict','outside_calibrated_regime')",
            name="ck_extraction_routing_outcome_enum",
        ),
    )

    def to_contract_dict(self) -> dict:
        """The shape `contracts/schemas/extraction.schema.json` requires.
        The DB columns are more permissive than the schema (e.g. `engine`
        is nullable here so a row can exist mid-assembly, before Shree's
        OCR has run) — this method is where a row is expected to have
        reached the schema's `required` set, and
        `services/backend/tests/contract/test_extraction_contract.py`
        checks exactly that: a fully-populated row validates, an
        incomplete one is a caller bug to catch before it reaches a queue
        message or an API response, not something this method silently
        papers over.
        """
        return {
            "id": self.id,
            "page_id": self.page_id,
            "field_name": self.field_name,
            "raw_value": self.raw_value,
            "canonical_value": self.canonical_value,
            "unconstrained_value": self.unconstrained_value,
            "bbox": self.bbox,
            "engine": self.engine,
            "model_version": self.model_version,
            "config_version": self.config_version,
            "token_confidence": self.token_confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "novelty_score": self.novelty_score,
            "routing_outcome": self.routing_outcome,
            "entry_status": self.entry_status,
            "attestation_refs": self.attestation_refs or [],
            "top_k": self.top_k or [],
        }


class RecordAssembly(Base):
    __tablename__ = "record_assembly"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    extraction_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False)  # single_page|continuation|carried_forward
    rationale: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)  # system|operator_id

    __table_args__ = (
        CheckConstraint(
            "strategy IN ('single_page','continuation','carried_forward')",
            name="ck_record_assembly_strategy_enum",
        ),
    )


class ParcelGeometry(Base):
    """`polygon` is `bytea` here — PostGIS 3.4 (ADR-006) is the target type
    but is not installed in this sandbox. TODO: flip to
    `geometry(Polygon, 4326)` the moment PostGIS is available; see
    ADR-006's Status section for exactly what changes."""

    __tablename__ = "parcel_geometry"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    page_id: Mapped[str] = mapped_column(String, ForeignKey("page.id"), nullable=False, index=True)
    polygon: Mapped[bytes | None] = mapped_column(String)  # TODO(ADR-006): geometry(Polygon, CRS)
    crs: Mapped[str | None] = mapped_column(String)
    area_computed: Mapped[float | None] = mapped_column(Float)
    control_points: Mapped[int | None] = mapped_column(Integer)
    georef_rmse: Mapped[float | None] = mapped_column(Float)  # FR-MAP-09
    transform_type: Mapped[str | None] = mapped_column(String)
    ulpin_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # FR-MAP-05
    bound_survey_no: Mapped[str | None] = mapped_column(String)
    conflation_lineage_ref: Mapped[str | None] = mapped_column(String)  # FR-MAP-12


class Record(Base):
    """P4-01/FR-PUB-01 — publication writes a new VERSION, never an
    update. `id` is this version's own row identity (what `ValidationResult
    .record_id`/`MutationEvent.source_record_id`/`LegacyRecordRef.record_id`
    reference — a specific version, per those tables' pre-existing FK
    meaning from Phase 1); `record_group_id` is the stable identity across
    versions of the same logical record — a publish always INSERTs a new
    row with the same `record_group_id` and `version = previous_max + 1`,
    never UPDATEs an existing row. Enforced two ways: `uq_record_group_version`
    (a given (group, version) pair can only exist once — no double-publish
    landing on the same version number) and a DB trigger rejecting any
    `UPDATE` on this table outright (migration 0005), the same pattern
    `work_envelope`'s immutability trigger uses (invariant 3). See
    PHASE4.md's "P4-01" section for why `record_group_id` was added rather
    than reusing `id` as the stable identity."""

    __tablename__ = "record"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    record_group_id: Mapped[str] = mapped_column(String, nullable=False, default=_uuid, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # FR-PUB-01, never overwritten
    parcel_ref: Mapped[str | None] = mapped_column(String)
    ulpin: Mapped[str | None] = mapped_column(String)
    lgd_codes: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str | None] = mapped_column(String)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("record_group_id", "version", name="uq_record_group_version"),
    )


class OwnerShare(Base):
    __tablename__ = "owner_share"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    record_id: Mapped[str] = mapped_column(String, ForeignKey("record.id"), nullable=False, index=True)
    # Exact rationals, never floats — FR-VAL-02 depends on this.
    share_numerator: Mapped[int] = mapped_column(Integer, nullable=False)
    share_denominator: Mapped[int] = mapped_column(Integer, nullable=False)
    relationship_: Mapped[str | None] = mapped_column("relationship", String)

    __table_args__ = (CheckConstraint("share_denominator <> 0", name="ck_owner_share_denominator_nonzero"),)


class MutationEvent(Base):
    __tablename__ = "mutation_event"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    parcel_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)
    transferor: Mapped[str | None] = mapped_column(String)
    transferee: Mapped[str | None] = mapped_column(String)
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_record_id: Mapped[str | None] = mapped_column(String, ForeignKey("record.id"))


class ValidationResult(Base):
    """Invariant 1 (CLAUDE.md, FR-VAL-09): `consumed_constraints` non-empty
    forces `verdict = 'not_applicable'`, enforced as a Postgres CHECK
    constraint below — a violating write raises, it does not warn."""

    __tablename__ = "validation_result"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String, ForeignKey("record.id"), nullable=False, index=True)
    validator: Mapped[str] = mapped_column(String, nullable=False)
    verdict: Mapped[str] = mapped_column(String, nullable=False)  # pass|fail|not_applicable
    reason_code: Mapped[str | None] = mapped_column(String)
    fields: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    config_version: Mapped[str | None] = mapped_column(String)
    consumed_constraints: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")

    __table_args__ = (
        CheckConstraint(
            "validator IN ('syntactic','arithmetic','referential','lineage','geospatial','legacy_reconciliation')",
            name="ck_validation_result_validator_enum",
        ),
        CheckConstraint("verdict IN ('pass','fail','not_applicable')", name="ck_validation_result_verdict_enum"),
        CheckConstraint(
            "cardinality(consumed_constraints) = 0 OR verdict = 'not_applicable'",
            name="ck_validation_result_consumed_constraints_forces_not_applicable",  # FR-VAL-09
        ),
    )


class ReviewTask(Base):
    """`source_stream` (invariant 4, CLAUDE.md / FR-REV-11) must never
    cross the API boundary — enforced at the serializer layer
    (`backend.api.serializers.strip_review_task_internals`), not here;
    this table legitimately stores the field, it just must never leave
    this process in a client-facing response."""

    __tablename__ = "review_task"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    extraction_id: Mapped[str] = mapped_column(String, ForeignKey("extraction.id"), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String)
    source_stream: Mapped[str] = mapped_column(String, nullable=False)  # routed|audit
    assignee: Mapped[str | None] = mapped_column(String)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cluster_id: Mapped[str | None] = mapped_column(String)
    cluster_size: Mapped[int | None] = mapped_column(Integer)
    hour_into_session: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint("source_stream IN ('routed','audit')", name="ck_review_task_source_stream_enum"),
    )


class AuditSample(Base):
    __tablename__ = "audit_sample"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    extraction_id: Mapped[str] = mapped_column(String, ForeignKey("extraction.id"), nullable=False, index=True)
    review_task_id: Mapped[str | None] = mapped_column(String, ForeignKey("review_task.id"))
    model_confidence: Mapped[float | None] = mapped_column(Float)
    officer_verdict: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    agreed: Mapped[bool | None] = mapped_column(Boolean)
    stratum: Mapped[str | None] = mapped_column(String)  # canonical stratum string — API-Contracts §6

    __table_args__ = (
        CheckConstraint("officer_verdict IN ('agree','disagree','pending')", name="ck_audit_sample_verdict_enum"),
    )


class RescanTask(Base):
    """M2, FR-TRI-01. Created from Tharun's legibility-threshold breach
    event (a fixture-backed fake stands in for that event —
    `backend.domain.rescan`; the legibility scorer itself is Tharun's, not
    backend's, per Team-Split). `state` lifecycle: open -> assigned ->
    closed, enforced below as a DB CHECK (added in migration 0003 for a
    pre-existing table; present from creation for a fresh one)."""

    __tablename__ = "rescan_task"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    page_id: Mapped[str] = mapped_column(String, ForeignKey("page.id"), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str | None] = mapped_column(String)  # PRD §11 Q4 — record room vs. this system
    state: Mapped[str] = mapped_column(String, nullable=False, server_default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "state IN ('open','assigned','closed')", name="ck_rescan_task_state_enum",
        ),
    )


class Conflict(Base):
    __tablename__ = "conflict"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    records: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    rule: Mapped[str] = mapped_column(String, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    origin: Mapped[str] = mapped_column(String, nullable=False)  # validator|downstream
    state: Mapped[str] = mapped_column(String, nullable=False, server_default="open")
    assignee: Mapped[str | None] = mapped_column(String)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        CheckConstraint("origin IN ('validator','downstream')", name="ck_conflict_origin_enum"),
        CheckConstraint(
            "state IN ('open','under_enquiry','resolved','referred')", name="ck_conflict_state_enum"
        ),
    )


class Correction(Base):
    __tablename__ = "correction"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    extraction_id: Mapped[str] = mapped_column(String, ForeignKey("extraction.id"), nullable=False, index=True)
    crop_uri: Mapped[str] = mapped_column(String, nullable=False)
    predicted: Mapped[str | None] = mapped_column(String)
    corrected: Mapped[str | None] = mapped_column(String)
    edit_distance: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String)
    config_version: Mapped[str | None] = mapped_column(String)
    stream: Mapped[str] = mapped_column(String, nullable=False)  # FR-LRN-07
    source_page_digest: Mapped[str] = mapped_column(String, nullable=False, index=True)  # FR-LRN-11
    reliability_weight: Mapped[float | None] = mapped_column(Float)  # FR-LRN-12, legacy_digital stream only
    # DB-only column, not part of contracts/schemas/correction.schema.json
    # (that schema is `additionalProperties: false` and frozen) — same
    # posture as Extraction's DB columns being a superset of its own
    # contract shape. P4-02: this is what lets `provenance.edit_history`
    # order a field's corrections chronologically.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        CheckConstraint(
            "stream IN ('routed','audit','downstream','legacy_digital')", name="ck_correction_stream_enum"
        ),
    )


class ModelVersion(Base):
    __tablename__ = "model_version"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    module: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    trained_on: Mapped[str | None] = mapped_column(String)  # dataset_snapshot_hash, duplicated below by name
    eval_results: Mapped[dict | None] = mapped_column(JSONB)
    calibration_results: Mapped[dict | None] = mapped_column(JSONB)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calibrator_ref: Mapped[str | None] = mapped_column(String)
    adapter_ref: Mapped[str | None] = mapped_column(String)  # FR-OCR-08, scoped to a writer cluster
    dataset_snapshot_hash: Mapped[str | None] = mapped_column(String)  # FR-LRN-11
    mde_at_promotion: Mapped[float | None] = mapped_column(Float)  # §09 minimum detectable effect

    __table_args__ = (
        CheckConstraint(
            "module IN ('printed_ocr','hwr','calibrator','novelty_detector','triage_classifier')",
            name="ck_model_version_module_enum",
        ),
    )


class ConfigVersion(Base):
    """FR-CFG-03: author must differ from approver — enforced below."""

    __tablename__ = "config_version"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String, nullable=False)  # district|state|global
    key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    author: Mapped[str] = mapped_column(String, nullable=False)
    approver: Mapped[str] = mapped_column(String, nullable=False)
    superseded_by: Mapped[str | None] = mapped_column(String, ForeignKey("config_version.id"))

    __table_args__ = (
        CheckConstraint("scope IN ('district','state','global')", name="ck_config_version_scope_enum"),
        CheckConstraint("author <> approver", name="ck_config_version_author_ne_approver"),  # FR-CFG-03
    )


class WriterCluster(Base):
    __tablename__ = "writer_cluster"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    batch_id: Mapped[str | None] = mapped_column(String, ForeignKey("batch.id"))
    style_embedding_centroid: Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    adapter_ref: Mapped[str | None] = mapped_column(String)
    # PRD §11 Q10: an unnamed style grouping — deliberately no name/officer
    # linkage column exists here. Do not add one without that question
    # being answered; see docs/open-questions.md.


class VolumeIndex(Base):
    __tablename__ = "volume_index"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(String, ForeignKey("batch.id"), nullable=False, index=True)
    expected_sequence: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    observed_sequence: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    gaps: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    state: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending','complete','gap_detected','acknowledged')",
            name="ck_volume_index_state_enum",
        ),
    )


class FixityCheck(Base):
    """FR-ING-07. `store` distinguishes which of the two independently-
    credentialed copies (`landstorage.get_store()` / `get_secondary_store()`)
    this row's check ran against — the sweep (`backend.domain.fixity`)
    checks both, since a compromised primary key rewriting the primary
    copy is exactly the failure mode a second, unchecked copy would miss."""

    __tablename__ = "fixity_check"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(String, ForeignKey("source_document.id"), nullable=False, index=True)
    expected_digest: Mapped[str] = mapped_column(String, nullable=False)
    observed_digest: Mapped[str | None] = mapped_column(String)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    store: Mapped[str] = mapped_column(String, nullable=False, server_default="primary")

    __table_args__ = (
        CheckConstraint("outcome IN ('match','mismatch')", name="ck_fixity_check_outcome_enum"),
        CheckConstraint("store IN ('primary','secondary')", name="ck_fixity_check_store_enum"),
    )


class ReviewSession(Base):
    """Phase 3 (P3-13, FR-REV-16). Backend-internal anchor for
    `hour_into_session` — not a `contracts/schemas/*.json` entity, because
    no other team's code reads or writes it (only `services/backend`'s own
    review workbench computes `ReviewTask.hour_into_session` from it,
    exactly as `INGESTION_QUEUE` in Phase 2 stayed out of
    `contracts/asyncapi/` for the identical reason — see PHASE3.md). A new
    session opens the first time an officer claims a task after
    `REVIEW_SESSION_GAP` of inactivity; `last_activity_at` advances on
    every claim so the gap is measured from real idle time, not wall clock
    since login."""

    __tablename__ = "review_session"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    actor: Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingCorrection(Base):
    """P3-07/FR-REV-12 maker–checker staging table. A high-edit-distance
    correction on a maker–checker field class lands here, never in
    `Correction`, until a second, distinct actor confirms it — the
    structural fix for "pending edits leaking into the learning loop"
    named in the build prompt: Tharun's training-store reader only ever
    queries `correction` (the frozen contract table), so a row that only
    exists here is absent from that read view by construction, not by
    convention. `Correction`'s own schema is frozen
    (`contracts/schemas/correction.schema.json`) and has no `state`/
    confirming-actor field, which is exactly why this couldn't be a status
    column on that table instead — see PHASE3.md."""

    __tablename__ = "pending_correction"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    extraction_id: Mapped[str] = mapped_column(String, ForeignKey("extraction.id"), nullable=False, index=True)
    crop_uri: Mapped[str] = mapped_column(String, nullable=False)
    predicted: Mapped[str | None] = mapped_column(String)
    corrected: Mapped[str | None] = mapped_column(String)
    edit_distance: Mapped[int] = mapped_column(Integer, nullable=False)
    field_class: Mapped[str] = mapped_column(String, nullable=False)
    first_actor: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str | None] = mapped_column(String)
    config_version: Mapped[str | None] = mapped_column(String)
    stream: Mapped[str] = mapped_column(String, nullable=False)
    source_page_digest: Mapped[str] = mapped_column(String, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    state: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    confirmed_by: Mapped[str | None] = mapped_column(String)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resulting_correction_id: Mapped[str | None] = mapped_column(String, ForeignKey("correction.id"))

    __table_args__ = (
        CheckConstraint("state IN ('pending','confirmed')", name="ck_pending_correction_state_enum"),
    )


class OperationalAlert(Base):
    """P3-02/FR-CNF-14. `outside_calibrated_regime` never creates a
    per-field `ReviewTask` — a *cluster* of novel pages raises exactly one
    of these, deduplicated on `cluster_key` within
    `NOVELTY_CLUSTER_ALERT_DEDUP_WINDOW` (`backend.domain.review_policy`).
    `cluster_key` is supplied by the caller (Tharun's novelty output names
    the cluster; backend does not cluster, per the ground rules) — see
    `backend.domain.decision._outside_calibrated_regime` for the
    fixture-backed fallback used until that field exists on a contract
    Tharun's side actually emits (PHASE3.md)."""

    __tablename__ = "operational_alert"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    cluster_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    extraction_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FieldProvenance(Base):
    """P4-02/FR-PUB-02 — the provenance data `Extraction` itself does not
    already carry. Deliberately NOT a duplicate of `Extraction.bbox` /
    `.engine` / `.model_version` / `.config_version` / `.raw_value` /
    `.token_confidence` — those already exist on the frozen-shape
    `Extraction` row (contracts/schemas/extraction.schema.json) and this
    table joins to it rather than re-storing them (one source of truth,
    no drift). What this table adds: `document_id` (a convenience
    denormalization — `Extraction.page_id -> Page.document_id` is a join
    away, but provenance navigation, P4's T4.e, wants it in one row) and
    the two transforms P4-02 specifically calls out: "store page-split and
    deskew transform alongside bbox so coordinates still resolve after
    preprocessing changes" — `Extraction.bbox` is pixel coordinates on the
    *split, deskewed* page image; without these transforms recorded, a
    future reprocessing run that changes deskew parameters would silently
    invalidate every already-published bbox with no way to tell.

    One row per `Extraction` (1:1, `extraction_id` unique) — provenance
    facts don't have their own version history the way field *values* do;
    a corrected value creates a new `Extraction`/`Correction`, which gets
    its own `FieldProvenance` row from whatever produced it.

    "Full edit history with actors" (P4-02's other requirement) is NOT a
    new table here — it already exists as `Correction` rows for this
    `extraction_id` (actor, predicted/corrected, edit_distance,
    `created_at`), reused rather than duplicated; see
    `backend.domain.provenance.resolve_provenance`.
    """

    __tablename__ = "field_provenance"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    extraction_id: Mapped[str] = mapped_column(
        String, ForeignKey("extraction.id"), nullable=False, unique=True, index=True
    )
    document_id: Mapped[str] = mapped_column(String, ForeignKey("source_document.id"), nullable=False, index=True)
    page_split_transform: Mapped[dict | None] = mapped_column(JSONB)  # backend.domain.page_split's own output
    deskew_transform: Mapped[dict | None] = mapped_column(JSONB)  # backend.domain.preprocessing's own output
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LegacyRecordRef(Base):
    """FR-VAL-08. Stays empty until PRD §11 Q3/Q9 are answered
    (docs/open-questions.md) — the table exists so the reweighting this
    entity feeds is not retrofitted later, not because it's populated now."""

    __tablename__ = "legacy_record_ref"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    record_id: Mapped[str] = mapped_column(String, ForeignKey("record.id"), nullable=False, index=True)
    external_key: Mapped[str | None] = mapped_column(String)
    external_values: Mapped[dict | None] = mapped_column(JSONB)
    disagreement_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
