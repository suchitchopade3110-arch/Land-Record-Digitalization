"""T5.c — the closed-set corpus-partition rule (contract §4.3, FR-VAL-09).

Same fixture convention as `services/backend/tests/contract/
test_config_version_storage_and_read.py`: real Postgres via
`TEST_DATABASE_URL`, skipped if 0007 isn't migrated yet.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from backend.domain.closed_sets import ClosedSetTypeNotFound, get_closed_set
from backend.domain.config_versions import (
    as_response,
    get_effective_config,
    get_pinned_config,
)
from backend.models.entities import ClosedSetEntry, ConfigVersion
from landconfigclient import ConfigClient
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
            exists = c.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = 'ck_closed_set_entry_provenance_enum'")
            ).scalar()
            if not exists:
                pytest.skip(f"{TEST_DB_URL} has no Phase 5 (0007) migrated schema — run migrations first")
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


def _mk_config_version(scope, key, *, author="alice", approver="bob", effective_from=None):
    return ConfigVersion(
        scope=scope,
        key=key,
        value={},
        effective_from=effective_from or (datetime.now(timezone.utc) - timedelta(days=1)),
        author=author,
        approver=approver,
    )


def _mk_entry(*, type_, config_version, code, provenance, district=None, source=None):
    return ClosedSetEntry(
        type=type_,
        district=district,
        code=code,
        source=source if source is not None else provenance,
        provenance=provenance,
        config_version=config_version,
    )


@pytest.fixture
def config_client_for(session):
    """A `landconfigclient.ConfigClient` reading through this test's own
    session, so tests can assert cache behaviour (test 5) precisely."""

    def _fetch(scope, key, config_version):
        row = (
            get_pinned_config(session, scope, key, config_version)
            if config_version is not None
            else get_effective_config(session, scope, key)
        )
        return as_response(row)

    return ConfigClient(fetch=_fetch)


# ---------------------------------------------------------------------------
# 1. Schema-derived set — codes, source="schema", the config_version it
#    came from.
# ---------------------------------------------------------------------------


def test_schema_derived_set_returned_with_source_and_config_version(session, config_client_for):
    pointer = _mk_config_version("global", "closed_set.land_use_category")
    session.add(pointer)
    session.flush()
    version_id = pointer.id

    session.add_all(
        [
            _mk_entry(type_="land_use_category", config_version=version_id, code="AGRI", provenance="schema"),
            _mk_entry(type_="land_use_category", config_version=version_id, code="RESI", provenance="schema"),
        ]
    )
    session.commit()

    result = get_closed_set(session, config_client_for, "land_use_category", None)
    assert result["codes"] == ["AGRI", "RESI"]
    assert result["source"] == "schema"
    assert result["config_version"] == version_id


# ---------------------------------------------------------------------------
# 2. LGD-derived set — source="lgd".
# ---------------------------------------------------------------------------


def test_lgd_derived_set_returned_with_source_lgd(session, config_client_for):
    pointer = _mk_config_version("district", "closed_set.village_name.sitapur")
    session.add(pointer)
    session.flush()
    version_id = pointer.id

    session.add_all(
        [
            _mk_entry(
                type_="village_name", config_version=version_id, code="V001",
                provenance="lgd", district="sitapur",
            ),
            _mk_entry(
                type_="village_name", config_version=version_id, code="V002",
                provenance="lgd", district="sitapur",
            ),
        ]
    )
    session.commit()

    result = get_closed_set(session, config_client_for, "village_name", "sitapur")
    assert result["codes"] == ["V001", "V002"]
    assert result["source"] == "lgd"
    assert result["config_version"] == version_id


# ---------------------------------------------------------------------------
# 3. An LRMS-derived row seeded with source mislabelled as "schema" is
#    still refused — the exclusion keys off provenance, not source.
# ---------------------------------------------------------------------------


def test_lrms_row_mislabelled_as_schema_source_is_still_excluded(session, config_client_for):
    pointer = _mk_config_version("global", "closed_set.tenure_type")
    session.add(pointer)
    session.flush()
    version_id = pointer.id

    session.add_all(
        [
            _mk_entry(type_="tenure_type", config_version=version_id, code="BHUMIDHAR", provenance="schema"),
            _mk_entry(type_="tenure_type", config_version=version_id, code="SIRDAR", provenance="schema"),
            # The attack T5.c exists for: source claims "schema", the
            # ground-truth provenance column says "lrms".
            _mk_entry(
                type_="tenure_type", config_version=version_id, code="LRMS_LEAKED_CODE",
                provenance="lrms", source="schema",
            ),
        ]
    )
    session.commit()

    result = get_closed_set(session, config_client_for, "tenure_type", None)
    assert "LRMS_LEAKED_CODE" not in result["codes"]
    assert result["codes"] == ["BHUMIDHAR", "SIRDAR"]
    assert result["source"] == "schema"


# ---------------------------------------------------------------------------
# 4. An unknown type returns 404 (raised here as ClosedSetTypeNotFound —
#    the API layer maps it to 404), not an empty set.
# ---------------------------------------------------------------------------


def test_unknown_type_raises_not_found_not_empty_set(session, config_client_for):
    with pytest.raises(ClosedSetTypeNotFound):
        get_closed_set(session, config_client_for, "type_that_has_never_been_configured", None)


# ---------------------------------------------------------------------------
# 5. The endpoint consumes libs/config_client: the cache is used, and a
#    config_version bump is reflected on the next call.
# ---------------------------------------------------------------------------


def test_consumes_config_client_cache_and_reflects_version_bump(session, config_client_for):
    fetch_calls = []
    real_fetch = config_client_for._fetch

    def counting_fetch(scope, key, config_version):
        fetch_calls.append((scope, key, config_version))
        return real_fetch(scope, key, config_version)

    config_client_for._fetch = counting_fetch

    pointer_v1 = _mk_config_version("global", "closed_set.season_code")
    session.add(pointer_v1)
    session.flush()
    v1_id = pointer_v1.id
    session.add(_mk_entry(type_="season_code", config_version=v1_id, code="KHARIF", provenance="schema"))
    session.commit()

    first = get_closed_set(session, config_client_for, "season_code", None)
    second = get_closed_set(session, config_client_for, "season_code", None)
    assert first == second
    assert len(fetch_calls) == 1, f"expected the second call to hit the cache, fetched {fetch_calls}"

    # A version bump: a newer ConfigVersion pointer takes effect, and a
    # matching batch of entries under the new config_version id.
    pointer_v2 = _mk_config_version(
        "global", "closed_set.season_code",
        effective_from=datetime.now(timezone.utc) + timedelta(seconds=-1),
    )
    session.add(pointer_v2)
    session.flush()
    v2_id = pointer_v2.id
    session.add(_mk_entry(type_="season_code", config_version=v2_id, code="RABI", provenance="schema"))
    session.commit()

    # Still cached until the invalidation event arrives.
    still_cached = get_closed_set(session, config_client_for, "season_code", None)
    assert still_cached["config_version"] == v1_id
    assert len(fetch_calls) == 1

    config_client_for.invalidate("global", "closed_set.season_code")

    third = get_closed_set(session, config_client_for, "season_code", None)
    assert third["config_version"] == v2_id
    assert third["codes"] == ["RABI"]
    assert len(fetch_calls) == 2
