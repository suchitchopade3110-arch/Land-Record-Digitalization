"""DB models. Shapes come from `contracts/schemas/*.json` where a schema
exists — this module is the SQLAlchemy mapping onto those shapes plus the
supporting entities from PRD §06, not an independent source of truth.
`landenvelope.WorkEnvelope` and `landoutbox.OutboxMessage` are mapped in
their own packages, not here — see `entities.py`'s module docstring.
"""
from backend.models.base import Base, engine_from_env, session_factory
from backend.models.entities import (
    AuditSample,
    Batch,
    ConfigVersion,
    Conflict,
    Correction,
    Extraction,
    FixityCheck,
    LegacyRecordRef,
    ModelVersion,
    MutationEvent,
    OwnerShare,
    Page,
    ParcelGeometry,
    Record,
    RecordAssembly,
    RescanTask,
    ReviewTask,
    SourceDocument,
    ValidationResult,
    VolumeIndex,
    WriterCluster,
)

__all__ = [
    "AuditSample",
    "Base",
    "Batch",
    "ConfigVersion",
    "Conflict",
    "Correction",
    "Extraction",
    "FixityCheck",
    "LegacyRecordRef",
    "ModelVersion",
    "MutationEvent",
    "OwnerShare",
    "Page",
    "ParcelGeometry",
    "Record",
    "RecordAssembly",
    "RescanTask",
    "ReviewTask",
    "SourceDocument",
    "ValidationResult",
    "VolumeIndex",
    "WriterCluster",
    "engine_from_env",
    "session_factory",
]
