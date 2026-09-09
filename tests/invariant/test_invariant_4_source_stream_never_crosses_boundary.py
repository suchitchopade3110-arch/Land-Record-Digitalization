"""Invariant 4 (CLAUDE.md) / FR-REV-11 — `ReviewTask.source_stream` never
crosses the API boundary, and a routed task and an audit-sampled task are
indistinguishable to the officer's client. `ReviewTaskPublicView`
structurally omits the field (no key to forget to strip); this test
byte-diffs the serialized response for a `routed` and an `audit` task that
are otherwise identical, and separately proves `source_stream` cannot
appear in the JSON at all.
"""
import json

from backend.api.serializers import strip_review_task_internals
from backend.models.entities import Extraction, ReviewTask


def _make_review_task(session, page, *, source_stream: str) -> ReviewTask:
    extraction = Extraction(page_id=page.id, field_name="owner_name", raw_value="x")
    session.add(extraction)
    session.flush()

    task = ReviewTask(
        extraction_id=extraction.id,
        reason="SHARE_OVERSUM: shares sum to 7/6",
        source_stream=source_stream,
        cluster_id=None,
        cluster_size=None,
        hour_into_session=3,
    )
    session.add(task)
    session.flush()
    return task, extraction


def test_source_stream_key_is_absent_from_the_serialized_response(session, a_page):
    task, _ = _make_review_task(session, a_page, source_stream="audit")

    view = strip_review_task_internals(task)
    serialized = json.loads(view.model_dump_json())

    assert "source_stream" not in serialized
    assert "source_stream" not in view.model_dump_json()  # also not present as raw text anywhere in the payload


def test_routed_and_audit_tasks_serialize_byte_identically_when_otherwise_equal(session, a_page):
    """FR-REV-11's actual requirement: not merely that the field is
    absent, but that an officer cannot distinguish a routed task from an
    audit-sample task by anything else in the response either."""
    routed_task, routed_extraction = _make_review_task(session, a_page, source_stream="routed")
    audit_task, audit_extraction = _make_review_task(session, a_page, source_stream="audit")

    routed_view = strip_review_task_internals(routed_task)
    audit_view = strip_review_task_internals(audit_task)

    # Normalize the two legitimately-different identifiers (id,
    # extraction_id, timestamp) so the comparison isolates whether
    # anything *else* leaks the stream — exactly what an officer's client
    # would see once IDs are opaque UUIDs it has no independent way to
    # correlate with "routed" or "audit".
    def _normalized(view):
        d = json.loads(view.model_dump_json())
        d["id"] = "NORMALIZED"
        d["extraction_id"] = "NORMALIZED"
        d["opened_at"] = "NORMALIZED"
        return d

    assert _normalized(routed_view) == _normalized(audit_view)


def test_public_view_schema_itself_has_no_source_stream_field():
    """A structural guarantee, not just an empirical one for these two
    rows: the Pydantic model backing every /review-tasks response has no
    field named source_stream at all."""
    from backend.api.serializers import ReviewTaskPublicView

    assert "source_stream" not in ReviewTaskPublicView.model_fields
