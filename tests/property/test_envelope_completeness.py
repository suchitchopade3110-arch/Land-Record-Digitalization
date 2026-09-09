"""T2.f — Envelope completeness. No message leaves triage without both
`model_version` and `config_version`. A property test over generated
classifier outputs, not a handful of examples — matching this repo's
existing property-test style (`test_masking_never_leaks_across_routes.py`:
loop over a generated space in pure Python, no extra test-only dependency
like `hypothesis`).

Runs the real `backend.domain.triage.route_page` (real Postgres, real
outbox writes) for every generated (doc_type, page_role) pair, rather than
asserting the property against a hand-built envelope — the whole point is
to catch a code path that could queue a lane without a complete envelope
attached, which a check against a fixture object built by the test itself
never could.

Uses `session.flush()`, not `.commit()` — every assertion reads through
the same open transaction, and the fixture's teardown `rollback()` then
leaves no permanent backlog in the shared test database (a real concern:
`tests/e2e`'s outbox relay drains the globally oldest undispatched rows
across every queue, uncapped by which test wrote them).
"""
import itertools
import os
import random

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.domain.triage import route_page
from backend.models.entities import Batch, Page, SourceDocument
from landenvelope.pin import REQUIRED_MODEL_KEYS
from landoutbox.models import OutboxMessage

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)

DOC_TYPES = ["ror", "jamabandi", "khasra_khatauni", "mutation_register", "deed", "cadastral_map", "fmb_sketch", "unknown"]
PAGE_ROLES = ["text", "tabular_register", "map_sheet", "endorsement", "blank"]


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


def _generate_classifier_outputs(seed: int, n: int):
    """A deterministic (seeded) shuffle of every (doc_type, page_role)
    combination — a property test still needs a reproducible run in CI,
    not a flaky one, so this is generation over a fixed space, not truly
    random fuzzing."""
    rng = random.Random(seed)
    combos = list(itertools.product(DOC_TYPES, PAGE_ROLES))
    rng.shuffle(combos)
    for doc_type, page_role in itertools.islice(itertools.cycle(combos), n):
        yield doc_type, page_role


def _new_page(session) -> Page:
    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()
    doc = SourceDocument(batch_id=batch.id, sha256=os.urandom(32).hex(), storage_uri="x", mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()
    page = Page(document_id=doc.id, index=0)
    session.add(page)
    session.flush()
    return page


def test_every_generated_classifier_output_produces_a_complete_envelope_on_every_queued_lane(session):
    for doc_type, page_role in _generate_classifier_outputs(seed=20260909, n=60):
        page = _new_page(session)

        envelope, queued_to = route_page(
            session, document_id=page.document_id, page_id=page.id,
            doc_type=doc_type, page_role=page_role, config_version="cfg-property-v1",
        )
        session.flush()

        contract_dict = envelope.to_contract_dict()
        assert set(contract_dict["model_versions"]) == REQUIRED_MODEL_KEYS, (doc_type, page_role)
        assert all(v for v in contract_dict["model_versions"].values()), (doc_type, page_role)
        assert contract_dict["config_version"], (doc_type, page_role)

        if page_role == "blank":
            assert queued_to == []
            continue

        assert queued_to  # every non-blank page reaches at least one lane
        for queue in queued_to:
            rows = [
                r for r in session.query(OutboxMessage).filter_by(queue=queue).all()
                if r.envelope["payload"]["page_id"] == page.id
            ]
            assert len(rows) == 1, (doc_type, page_role, queue)
            work_envelope = rows[0].envelope["work_envelope"]
            assert set(work_envelope["model_versions"]) == REQUIRED_MODEL_KEYS
            assert all(v for v in work_envelope["model_versions"].values())
            assert work_envelope["config_version"]
