"""Invariant 2 (CLAUDE.md) / FR-EXT-06/07 — `entry_status` defaults to
`unknown`, never `live`; the only application-level path to `live` writes
an audit event in the same transaction."""
import pytest
from sqlalchemy.exc import IntegrityError

from backend.domain.entry_status import InvalidEntryStatusTransition, mark_live
from backend.models.entities import Extraction


def _make_extraction(session, page, **overrides):
    extraction = Extraction(
        page_id=page.id, field_name="owner_name", raw_value="राम प्रसाद", **overrides
    )
    session.add(extraction)
    session.flush()
    return extraction


def test_entry_status_defaults_to_unknown_when_not_specified(session, a_page):
    extraction = _make_extraction(session, a_page)
    session.refresh(extraction)

    assert extraction.entry_status == "unknown"


def test_entry_status_is_never_null(session, a_page):
    with pytest.raises(IntegrityError):
        session.execute(
            Extraction.__table__.insert().values(
                page_id=a_page.id, field_name="owner_name", raw_value="x", entry_status=None
            )
        )
        session.flush()


def test_entry_status_rejects_a_value_outside_the_enum(session, a_page):
    session.add(
        Extraction(page_id=a_page.id, field_name="owner_name", raw_value="x", entry_status="published")
    )
    with pytest.raises(IntegrityError, match="ck_extraction_entry_status_enum"):
        session.flush()


def test_mark_live_writes_an_audit_event_in_the_same_transaction(session, a_page):
    from landaudit.chain import shard_for
    from landaudit.models import AuditEntry

    extraction = _make_extraction(session, a_page)
    assert extraction.entry_status == "unknown"

    mark_live(session, extraction.id, actor="officer1", reason_code="no strikethrough detected")

    assert extraction.entry_status == "live"
    audit_row = (
        session.query(AuditEntry)
        .filter_by(shard_id=shard_for(extraction.id), subject=extraction.id, action="extraction.entry_status.mark_live")
        .one_or_none()
    )
    assert audit_row is not None
    assert audit_row.actor == "officer1"
    assert audit_row.purpose == "no strikethrough detected"


def test_mark_live_refuses_a_second_transition_from_a_non_unknown_state(session, a_page):
    extraction = _make_extraction(session, a_page)
    mark_live(session, extraction.id, actor="officer1", reason_code="r1")

    with pytest.raises(InvalidEntryStatusTransition):
        mark_live(session, extraction.id, actor="officer2", reason_code="r2")


def test_mark_live_is_the_only_place_in_services_backend_that_assigns_entry_status_live():
    """Static check backing the module docstring's convention: a
    `grep -rn 'entry_status.*=.*"live"'` outside `entry_status.py` is a
    review finding. This test makes that finding automatic instead of
    relying on a reviewer remembering to look."""
    import re
    from pathlib import Path

    backend_src = Path(__file__).resolve().parents[2] / "services" / "backend" / "src"
    pattern = re.compile(r"""entry_status\s*=\s*["']live["']""")

    offending = []
    for path in backend_src.rglob("*.py"):
        if path.name == "entry_status.py":
            continue
        if pattern.search(path.read_text()):
            offending.append(str(path))

    assert offending == [], f"entry_status assigned to 'live' outside entry_status.py: {offending}"
