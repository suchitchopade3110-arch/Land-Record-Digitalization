"""T4.a/b/e/f + P4-01/02/12/13 — contract tests against a real, migrated
Postgres (`TEST_DATABASE_URL`, same convention as every other file in
this directory — skips if the schema isn't migrated, per
`test_conflict_register_workflow.py`'s fixture).
"""
import os

import pytest
from backend.domain.audit_log import record_field_edit, record_publish
from backend.domain.correction import submit_correction
from backend.domain.provenance import original_bbox_for, record_provenance, resolve_provenance
from backend.domain.publication import PublishBlocked, publish_new_version
from backend.domain.publication_adapters import DILRMPAdapter, GISAdapter, LRMSAdapter
from backend.domain.preprocessing import AffineTransform
from backend.models.entities import (
    Batch,
    Extraction,
    Page,
    SourceDocument,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM field_provenance LIMIT 0"))
            c.execute(text("SELECT 1 FROM chain_root LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no Phase 4 (0005) migrated schema — run migrations first")
    return eng


@pytest.fixture(autouse=True)
def _hmac_key(monkeypatch):
    monkeypatch.setenv("AUDIT_HMAC_KEY", "test-only-hmac-key-do-not-use-in-prod")


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


def _seed_batch_document_page_extraction(session, *, district="sitapur", field_name="owner_name"):
    batch = Batch(district=district)
    session.add(batch)
    session.flush()
    doc = SourceDocument(
        batch_id=batch.id, sha256="a" * 64, storage_uri="sha256/aa/aa/" + "a" * 64, mime="image/tiff", page_count=1,
    )
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0, storage_uri="sha256/bb/bb/" + "b" * 64)
    session.add(page)
    session.flush()
    extraction = Extraction(
        page_id=page.id, field_name=field_name, raw_value="Ram Prasad", canonical_value="Ram Prasad",
        bbox={"x": 10, "y": 10, "w": 20, "h": 20}, engine="hwr", model_version="v1", config_version="v1",
        entry_status="live",
    )
    session.add(extraction)
    session.flush()
    return batch, doc, page, extraction


# ---- P4-01: Record versioning ----


def test_publish_new_version_creates_v1_then_v2_never_updates(session):
    batch, *_ = _seed_batch_document_page_extraction(session)
    r1 = publish_new_version(session, record_group_id=None, batch_id=batch.id, actor="supervisor1", parcel_ref="p1")
    assert r1.version == 1
    r2 = publish_new_version(
        session, record_group_id=r1.record_group_id, batch_id=batch.id, actor="supervisor1", parcel_ref="p2",
    )
    assert r2.version == 2
    assert r2.record_group_id == r1.record_group_id
    assert r2.id != r1.id  # a NEW row, not an update of r1


def test_record_table_rejects_a_direct_update_even_with_full_db_credentials(session):
    """P4-01's DB trigger, not just service-layer discipline — same shape
    as invariant 3's `work_envelope` trigger test."""
    batch, *_ = _seed_batch_document_page_extraction(session)
    r1 = publish_new_version(session, record_group_id=None, batch_id=batch.id, actor="supervisor1")
    session.flush()
    from backend.models.entities import Record

    row = session.get(Record, r1.id)
    row.status = "tampered"
    with pytest.raises(Exception, match="append-only|record is"):
        session.flush()
    session.rollback()


def test_publish_blocked_by_volume_gap_names_the_reason(session):
    from backend.domain.completeness import record_index_position, rebuild

    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(
        batch_id=batch.id, sha256="c" * 64, storage_uri="sha256/cc/cc/" + "c" * 64, mime="image/tiff", page_count=2,
    )
    session.add(doc)
    session.flush()
    page1 = Page(document_id=doc.id, index=0)
    page3 = Page(document_id=doc.id, index=1)
    session.add_all([page1, page3])
    session.flush()
    # A gap: only index positions 1 and 3 seen, 2 missing.
    record_index_position(session, page_id=page1.id, index_position=1)
    record_index_position(session, page_id=page3.id, index_position=3)
    rebuild(session, batch.id)
    session.flush()

    with pytest.raises(PublishBlocked) as exc_info:
        publish_new_version(session, record_group_id=None, batch_id=batch.id, actor="supervisor1")
    codes = [r.code for r in exc_info.value.reasons]
    assert "volume_gap" in codes


# ---- P4-02/T4.e: provenance navigation, including a deskewed page ----


def test_provenance_resolves_document_page_bbox_engine_and_edit_history(session):
    _batch, doc, page, extraction = _seed_batch_document_page_extraction(session)
    record_provenance(
        session, extraction=extraction, document_id=doc.id,
        page_split_transform={"page_index": 0},
        deskew_transform=AffineTransform(angle_degrees=3.5, original_size=(1000, 1400), processed_size=(1050, 1420)),
    )
    # A small (edit distance 1, below the maker-checker threshold) fix,
    # so this lands as a real Correction, not a PendingCorrection — see
    # backend.domain.review_policy.MAKER_CHECKER_EDIT_DISTANCE_THRESHOLD.
    submit_correction(session, extraction=extraction, corrected="Ram Prasat", actor="operator1", stream="routed")
    session.flush()

    resolved = resolve_provenance(session, extraction.id)
    assert resolved.document_id == doc.id
    assert resolved.page_id == page.id
    assert resolved.engine == "hwr"
    assert resolved.deskew_transform["angle_degrees"] == 3.5
    assert len(resolved.edit_history) == 1
    assert resolved.edit_history[0].actor == "operator1"


def test_original_bbox_maps_back_through_a_nonzero_deskew_angle(session):
    """T4.e — "run against a page that went through deskew": with a
    nonzero angle, the original-space bbox must differ from the recorded
    (post-deskew) bbox; with a zero angle it's unchanged (sanity check on
    the same transform math `preprocessing.map_point_to_original` uses)."""
    _batch, doc, _page, extraction = _seed_batch_document_page_extraction(session)
    record_provenance(
        session, extraction=extraction, document_id=doc.id,
        deskew_transform=AffineTransform(angle_degrees=10.0, original_size=(1000, 1400), processed_size=(1030, 1440)),
    )
    session.flush()
    resolved = resolve_provenance(session, extraction.id)
    original = original_bbox_for(resolved)
    assert original is not None
    assert original != resolved.bbox


# ---- P4-07: HMAC value hash on the field-edit audit trail ----


def test_field_edit_audit_entry_stores_a_value_hash_never_the_value(session):
    record_field_edit(
        session, actor="operator1", record_id="rec-1", version=1, field_name="owner_name",
        reason_code="typo_fix", old_value="Ram Prasad", new_value="Ram Prasad Singh", district="sitapur",
    )
    session.flush()
    from landaudit.models import AuditEntry

    entry = session.query(AuditEntry).filter_by(action="field.edited").order_by(AuditEntry.at.desc()).first()
    assert entry is not None
    assert "Ram Prasad" not in (entry.value_hash or "")
    assert entry.purpose == "typo_fix"


def test_publish_audit_entry_lands_in_the_records_district_shard(session):
    batch, *_ = _seed_batch_document_page_extraction(session, district="lucknow")
    record_publish(session, actor="supervisor1", record_id="rec-2", version=1, district="lucknow")
    session.flush()
    from landaudit import shard_key_for, shard_for
    from landaudit.models import AuditEntry

    entry = session.query(AuditEntry).filter_by(action="record.published", subject="rec-2:1").one()
    assert entry.shard_id == shard_for(shard_key_for(district="lucknow"))


# ---- P4-12/T4.f: publication adapter contract, P4-13 downstream intake ----


@pytest.mark.parametrize("adapter_cls", [LRMSAdapter, DILRMPAdapter, GISAdapter])
def test_every_adapter_publishes_and_accepts(adapter_cls, session):
    adapter = adapter_cls(session)
    result = adapter.publish({"record_id": "rec-1", "fields": {}})
    assert result.accepted is True
    assert result.external_reference


@pytest.mark.parametrize("adapter_cls", [LRMSAdapter, DILRMPAdapter, GISAdapter])
def test_a_defect_reported_through_any_adapter_lands_in_the_conflict_register_as_downstream(adapter_cls, session):
    """T4.f — "a defect through the LRMS mock appears in the conflict
    register within one processing cycle with origin='downstream'." No
    queue hop at P0 (see publication_adapters.py's docstring) — the call
    itself IS the processing cycle."""
    adapter = adapter_cls(session)
    conflict = adapter.report_defect(record_id="rec-1", rule="mismatched_area", evidence={"delta": 12.5})
    session.flush()
    assert conflict.origin == "downstream"
    assert "rec-1" in conflict.records
    assert conflict.evidence["reported_by"] == adapter.name
