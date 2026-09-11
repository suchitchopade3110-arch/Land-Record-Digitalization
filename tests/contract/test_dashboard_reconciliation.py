"""T5.d — dashboard reconciliation (M14/P5-06, FR-ANL-01/07, CLAUDE.md's
"one source of truth" rule). Every figure `backend.domain.dashboard.
get_page_activity` returns must equal an independent direct query over
`audit_entry`, written separately here rather than by calling the
endpoint's own helper — a shared helper would make this tautological.

Real Postgres via `TEST_DATABASE_URL`, skipped if 0009 isn't migrated.
Rows are constructed directly against `AuditEntry` (bypassing
`landaudit.append`'s hash-chaining) — this suite is about the dashboard's
query/aggregation logic, not chain integrity (T4.a/`libs/audit/tests/
test_chain.py` own that).
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from backend.domain.dashboard import PAGE_ACTIVITY_UNITS, get_page_activity
from backend.domain.decision import route
from backend.domain.page_lifecycle import mark_processed_if_terminal
from backend.domain.triage import route_page
from backend.models.entities import Batch, Extraction, Page, SourceDocument
from landenvelope.pin import REQUIRED_MODEL_KEYS
from landaudit.chain import shard_for, shard_key_for
from landaudit.models import AuditEntry
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT district FROM audit_entry LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no Phase 5 (0009) migrated schema — run migrations first")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


def _seed(session, *, action, district, at, subject=None):
    """Direct `AuditEntry` construction — see module docstring for why
    this doesn't go through `landaudit.append`."""
    row = AuditEntry(
        shard_id=shard_for(shard_key_for(district=district)),
        prev_hash=None,
        hash=f"test-hash-{uuid.uuid4()}",
        actor="system:test-seed",
        action=action,
        subject=subject or str(uuid.uuid4()),
        district=district,
        at=at,
    )
    session.add(row)
    return row


def _direct_count(session, *, action, district, day) -> int:
    """The independent query T5.d requires — written separately from
    `get_page_activity`, not by calling it or a shared helper."""
    return session.execute(
        select(func.count())
        .select_from(AuditEntry)
        .where(
            AuditEntry.action == action,
            AuditEntry.district == district,
            func.date(AuditEntry.at) == day,
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# 1/2. Pages ingested/processed/published, by district and over time —
# each figure equals an independent direct query; seeding more moves it.
# ---------------------------------------------------------------------------


def test_figures_equal_independent_direct_queries_and_move_when_seeded_further(session):
    district = f"reconciliation-test-district-{uuid.uuid4().hex[:8]}"
    day1 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)

    _seed(session, action="page.ingested", district=district, at=day1)
    _seed(session, action="page.ingested", district=district, at=day1)
    _seed(session, action="page.processed", district=district, at=day1)
    _seed(session, action="record.published", district=district, at=day1)
    session.commit()

    rows = {r["date"]: r for r in get_page_activity(session, district=district)}
    assert rows["2026-03-01"]["pages_ingested"] == _direct_count(
        session, action="page.ingested", district=district, day=day1.date()
    ) == 2
    assert rows["2026-03-01"]["pages_processed"] == _direct_count(
        session, action="page.processed", district=district, day=day1.date()
    ) == 1
    assert rows["2026-03-01"]["records_published"] == _direct_count(
        session, action="record.published", district=district, day=day1.date()
    ) == 1
    assert "2026-03-02" not in rows  # nothing seeded that day yet — absent, not zero

    # Seed more, on a different day — the figure must move, and a new day
    # must appear.
    _seed(session, action="page.ingested", district=district, at=day1)
    _seed(session, action="page.ingested", district=district, at=day2)
    session.commit()

    rows_after = {r["date"]: r for r in get_page_activity(session, district=district)}
    assert rows_after["2026-03-01"]["pages_ingested"] == 3
    assert rows_after["2026-03-02"]["pages_ingested"] == 1
    assert rows_after["2026-03-01"]["pages_ingested"] == _direct_count(
        session, action="page.ingested", district=district, day=day1.date()
    )


# ---------------------------------------------------------------------------
# 3. A figure derived from anything but audit_entry fails this suite — no
# such second source exists in this codebase, so this proves the
# implementation doesn't accidentally behave like one would (e.g. by
# counting every row for a district regardless of action, which WOULD
# drift the moment an unrelated action is seeded).
# ---------------------------------------------------------------------------


def test_implementation_does_not_drift_like_a_naive_second_source_would(session):
    district = f"reconciliation-drift-test-{uuid.uuid4().hex[:8]}"
    day = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)

    _seed(session, action="page.ingested", district=district, at=day)
    session.commit()

    before = {r["date"]: r for r in get_page_activity(session, district=district)}
    assert before["2026-04-01"]["pages_ingested"] == 1
    assert before["2026-04-01"]["pages_processed"] == 0
    assert before["2026-04-01"]["records_published"] == 0

    # Seed a divergent, unrelated audit_entry row for the SAME district and
    # day — a "count every row for this district/day" implementation
    # (the naive second-source-of-truth this test exists to rule out)
    # would now report 2 pages_ingested; the real implementation, which
    # filters by action, must not.
    _seed(session, action="rbac.checked", district=district, at=day)
    session.commit()

    after = {r["date"]: r for r in get_page_activity(session, district=district)}
    assert after["2026-04-01"]["pages_ingested"] == 1, "drifted — behaves like a naive count-every-row source"


# ---------------------------------------------------------------------------
# 4. Sharded chain correctness — figures must aggregate across every
# audit_entry shard, not just one. Seed into two districts verified (via
# shard_for directly) to land in different shards.
# ---------------------------------------------------------------------------


def test_aggregates_across_multiple_shards_not_just_one(session):
    day = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)

    # Find two district names that genuinely hash to different shards —
    # never assume; a coincidental collision would make this test pass
    # vacuously.
    candidates = [f"shard-test-district-{i}" for i in range(32)]
    shard_a_district = candidates[0]
    shard_a = shard_for(shard_key_for(district=shard_a_district))
    shard_b_district = next(
        d for d in candidates[1:] if shard_for(shard_key_for(district=d)) != shard_a
    )
    shard_b = shard_for(shard_key_for(district=shard_b_district))
    assert shard_a != shard_b

    _seed(session, action="page.ingested", district=shard_a_district, at=day)
    _seed(session, action="page.ingested", district=shard_a_district, at=day)
    _seed(session, action="page.ingested", district=shard_b_district, at=day)
    session.commit()

    rows_a = {r["date"]: r for r in get_page_activity(session, district=shard_a_district)}
    rows_b = {r["date"]: r for r in get_page_activity(session, district=shard_b_district)}
    assert rows_a["2026-05-01"]["pages_ingested"] == 2
    assert rows_b["2026-05-01"]["pages_ingested"] == 1

    # And unfiltered (no district), both shards' contributions are present.
    all_rows = {(r["district"], r["date"]): r for r in get_page_activity(session, since=day, until=datetime(2026, 5, 2, tzinfo=timezone.utc))}
    assert all_rows[(shard_a_district, "2026-05-01")]["pages_ingested"] == 2
    assert all_rows[(shard_b_district, "2026-05-01")]["pages_ingested"] == 1


# ---------------------------------------------------------------------------
# 5. The endpoint respects masking and enters the P4-10b live-route sweep
# — verified in tests/property/test_live_route_masking.py directly (the
# route is no longer excluded/skipped there now that it's live). Recorded
# here as a pointer, not duplicated: aggregate counts carry no
# per-record personal data by construction (district/date/counts only),
# so there is nothing for the masking policy to act on in the first
# place — confirmed structurally by this module's own response shape.
# ---------------------------------------------------------------------------


def test_response_shape_carries_no_per_record_fields_to_mask(session):
    district = f"masking-shape-test-{uuid.uuid4().hex[:8]}"
    _seed(session, action="page.ingested", district=district, at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    session.commit()

    rows = get_page_activity(session, district=district)
    assert rows
    for row in rows:
        assert set(row.keys()) == {"district", "date", "pages_ingested", "pages_processed", "records_published"}


# ---------------------------------------------------------------------------
# 6. P5-06-fix — "processed" means a terminal decision, not triage
# routing. Exercised through the real domain functions (not seeded
# AuditEntry rows) so this proves the actual call sites, not just the
# aggregation query above them.
# ---------------------------------------------------------------------------


@pytest.fixture
def a_page(session):
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256="d" * 64, storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0)
    session.add(page)
    session.flush()
    return page


def _extraction(session, page, routing_outcome=None):
    e = Extraction(page_id=page.id, field_name="owner_name", raw_value="x", routing_outcome=routing_outcome)
    session.add(e)
    session.flush()
    return e


def _page_processed_count(session, page_id) -> int:
    return session.query(AuditEntry).filter_by(action="page.processed", subject=page_id).count()


def test_triaged_but_not_decided_page_does_not_count_as_processed(session, a_page):
    # Triage runs (envelope pinned, queued) — this alone must never mark
    # the page processed; see backend.domain.triage.route_page's
    # docstring for why it no longer emits page.processed at all.
    route_page(
        session, document_id=a_page.document_id, page_id=a_page.id,
        doc_type="jamabandi", page_role="text", config_version="v1",
        # T1-04 made the real Model Registry HTTP client route_page's
        # default resolver — this test is about page.processed audit
        # semantics, not model resolution, so it stays off the network.
        resolve_model_versions=lambda: dict.fromkeys(REQUIRED_MODEL_KEYS, "stub-v0"),
    )
    _extraction(session, a_page, routing_outcome=None)  # extracted but not yet decided
    session.commit()

    assert _page_processed_count(session, a_page.id) == 0


def test_page_with_one_of_two_fields_decided_is_not_processed(session, a_page):
    e1 = _extraction(session, a_page, routing_outcome="auto_accept")
    _extraction(session, a_page, routing_outcome=None)  # second field still mid-pipeline

    route(session, e1.id)
    session.commit()

    assert _page_processed_count(session, a_page.id) == 0


def test_page_processed_fires_once_last_field_reaches_terminal_outcome(session, a_page):
    e1 = _extraction(session, a_page, routing_outcome="auto_accept")
    e2 = _extraction(session, a_page, routing_outcome=None)  # not decided yet

    route(session, e1.id)
    assert _page_processed_count(session, a_page.id) == 0  # e2 still non-terminal

    # e2's routing_outcome arrives later (M8's classifier decides it) —
    # decision.route() only ever acts on an already-set outcome.
    e2.routing_outcome = "review"
    session.flush()
    route(session, e2.id)
    session.commit()

    assert _page_processed_count(session, a_page.id) == 1


def test_page_processed_replay_guard_fires_exactly_once(session, a_page):
    e1 = _extraction(session, a_page, routing_outcome="auto_accept")
    route(session, e1.id)
    session.commit()
    assert _page_processed_count(session, a_page.id) == 1

    # A redelivered decision message for the same (already-terminal) field.
    route(session, e1.id)
    route(session, e1.id)
    session.commit()

    assert _page_processed_count(session, a_page.id) == 1


def test_mark_processed_if_terminal_returns_whether_it_appended(session, a_page):
    _extraction(session, a_page, routing_outcome="auto_accept")
    session.flush()

    assert mark_processed_if_terminal(session, a_page.id) is True
    assert mark_processed_if_terminal(session, a_page.id) is False  # already recorded


# ---------------------------------------------------------------------------
# 7. P5-06-units — the response declares which unit each key counts,
# explicitly, so `records_published` can't be silently misread as a page
# count just because it sits next to two genuine page counts.
# ---------------------------------------------------------------------------


def test_units_mapping_matches_the_actual_response_keys(session):
    district = f"units-test-{uuid.uuid4().hex[:8]}"
    _seed(session, action="page.ingested", district=district, at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    session.commit()

    rows = get_page_activity(session, district=district)
    row_keys = set(rows[0].keys()) - {"district", "date"}

    assert set(PAGE_ACTIVITY_UNITS.keys()) == row_keys
    assert PAGE_ACTIVITY_UNITS["pages_ingested"] == "pages"
    assert PAGE_ACTIVITY_UNITS["pages_processed"] == "pages"
    assert PAGE_ACTIVITY_UNITS["records_published"] == "records"
