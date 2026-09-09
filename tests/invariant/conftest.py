"""Fixtures for the four §5 invariant tests (CLAUDE.md). Runs against a
real local Postgres — `landrecords_test` by default, matching
`infra/docker-compose.yml`'s credentials — with `infra/migrations` already
applied (the Makefile's `verify` target runs `alembic upgrade head` against
this DB before this suite; see Makefile's `test-invariant` target for how
to run it standalone).
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM work_envelope LIMIT 0"))
    except Exception:
        pytest.skip(
            f"{TEST_DB_URL} has no migrated schema — run "
            "`DATABASE_URL=$TEST_DATABASE_URL alembic -c infra/migrations/alembic.ini upgrade head` first "
            "(the Makefile's `verify`/`test-invariant` targets do this automatically)"
        )
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()  # each test's writes are isolated from the next test


@pytest.fixture
def a_page(session):
    """A minimal Batch -> SourceDocument -> Page chain, since Extraction,
    ValidationResult, etc. all FK down to a real Page/Record."""
    from backend.models.entities import Batch, Page, SourceDocument

    batch = Batch(district="sitapur")
    session.add(batch)
    session.flush()

    doc = SourceDocument(batch_id=batch.id, sha256="a" * 64, storage_uri="sha256/aa/aa/" + "a" * 64, mime="image/tiff", page_count=1)
    session.add(doc)
    session.flush()

    page = Page(document_id=doc.id, index=0)
    session.add(page)
    session.flush()
    return page


@pytest.fixture
def a_record(session):
    from backend.models.entities import Record

    record = Record(version=1)
    session.add(record)
    session.flush()
    return record
