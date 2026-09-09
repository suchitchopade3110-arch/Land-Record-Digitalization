"""T2.d — Replay determinism, the single most important test in this
phase (FR-TRI-09). Replay a triaged message after bumping the active
model version in the registry fake. The result is byte-identical to the
first run and the envelope still names the old version.

This proves `backend.domain.triage.route_page` / `landenvelope.pin.pin`'s
get-or-create-by-page_id behavior actually delivers what
"never resolve current model/config mid-pipeline" (API-Contracts §7 rule
1) requires: a retried/redelivered triage message must reproduce its
original result, not silently pick up whatever the registry says *now*.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.domain.triage import route_page
from backend.models.entities import Batch, Page, SourceDocument
from landoutbox.models import OutboxMessage

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM work_envelope LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture
def a_page(session):
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256="e" * 64, storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0)
    session.add(page)
    session.flush()
    return page


class _FakeModelRegistry:
    """Stands in for Tharun's `GET /models/{module}/active` (API-Contracts
    §4.2) — a mutable "currently active version" a test can bump mid-run,
    the exact shape T2.d needs to prove replay ignores it."""

    def __init__(self, versions: dict[str, str]):
        self._versions = dict(versions)
        self.call_count = 0

    def resolve(self) -> dict[str, str]:
        self.call_count += 1
        return dict(self._versions)

    def bump(self, module: str, new_version: str) -> None:
        self._versions[module] = new_version


def test_replaying_a_triaged_message_after_a_model_promotion_reproduces_the_original_envelope(session, a_page):
    registry = _FakeModelRegistry(
        {
            "triage_classifier": "v1", "printed_ocr": "v1", "hwr": "v1",
            "confidence_calibrator": "v1", "novelty_detector": "v1",
        }
    )

    first_envelope, first_queued = route_page(
        session, document_id=a_page.document_id, page_id=a_page.id,
        doc_type="jamabandi", page_role="text", config_version="cfg-v1",
        resolve_model_versions=registry.resolve,
    )
    session.flush()
    calls_after_first_run = registry.call_count

    first_envelope_id = first_envelope.envelope_id
    first_model_versions = dict(first_envelope.model_versions)
    first_contract_dict = first_envelope.to_contract_dict()

    # The active model registry is bumped — simulating a promotion that
    # happens between the original triage run and a redelivered/replayed
    # message for the *same* page (e.g. an at-least-once redelivery after
    # a worker crash mid-handle, ADR-005).
    registry.bump("printed_ocr", "v2-should-never-be-pinned")

    second_envelope, second_queued = route_page(
        session, document_id=a_page.document_id, page_id=a_page.id,
        doc_type="jamabandi", page_role="text", config_version="cfg-v1",
        resolve_model_versions=registry.resolve,
    )
    session.flush()

    # The replay must not even ask the registry again — "never resolve
    # current model mid-pipeline" means not making the call, not just
    # discarding its answer.
    assert registry.call_count == calls_after_first_run

    assert second_envelope.envelope_id == first_envelope_id
    assert second_envelope.model_versions == first_model_versions
    assert second_envelope.model_versions["printed_ocr"] == "v1"  # still the OLD version
    assert second_envelope.to_contract_dict() == first_contract_dict  # byte-identical
    assert second_queued == first_queued

    # And there is still exactly one WorkEnvelope row for this page — the
    # replay did not pin a second one.
    from landenvelope.models import WorkEnvelope

    assert session.query(WorkEnvelope).filter_by(page_id=a_page.id).count() == 1


def test_a_direct_second_pin_call_for_the_same_page_is_also_idempotent(session, a_page):
    """Defense in depth: `landenvelope.pin.pin` itself is idempotent by
    page_id, not just `route_page`'s pre-check — a future caller that
    calls `pin()` directly (bypassing `route_page`) still can't create a
    second envelope for the same page."""
    from landenvelope import pin

    valid_versions = {
        "triage_classifier": "v1", "printed_ocr": "v1", "hwr": "v1",
        "confidence_calibrator": "v1", "novelty_detector": "v1",
    }
    different_versions = dict(valid_versions, printed_ocr="v9-different")

    first = pin(session, document_id=a_page.document_id, page_id=a_page.id, model_versions=valid_versions, config_version="cfg-v1")
    session.flush()

    second = pin(session, document_id=a_page.document_id, page_id=a_page.id, model_versions=different_versions, config_version="cfg-v9")
    session.flush()

    assert second.envelope_id == first.envelope_id
    assert second.model_versions == valid_versions  # the different_versions argument was ignored
    assert second.config_version == "cfg-v1"


def test_the_database_itself_refuses_two_envelopes_for_the_same_page(session, a_page):
    """The DB-level backing for the guarantee above — a unique constraint
    on work_envelope.page_id (migration 0003), not just an application-
    level check `pin()` happens to make."""
    from sqlalchemy.exc import IntegrityError

    from landenvelope.models import WorkEnvelope

    valid_versions = {
        "triage_classifier": "v1", "printed_ocr": "v1", "hwr": "v1",
        "confidence_calibrator": "v1", "novelty_detector": "v1",
    }
    session.add(WorkEnvelope(document_id=a_page.document_id, page_id=a_page.id, model_versions=valid_versions, config_version="cfg-v1"))
    session.flush()

    session.add(WorkEnvelope(document_id=a_page.document_id, page_id=a_page.id, model_versions=valid_versions, config_version="cfg-v2"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
