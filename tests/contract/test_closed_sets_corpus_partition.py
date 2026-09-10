"""T5.c — the closed-set corpus-partition rule (contract §4.3, FR-VAL-09).

Same fixture convention as `services/backend/tests/contract/
test_config_version_storage_and_read.py`: real Postgres via
`TEST_DATABASE_URL`, skipped if 0008 isn't migrated yet.

P5-05-fix: legitimate ('schema'/'lgd') rows below go through
`backend.domain.closed_set_loaders` — the only sanctioned writers — not
through `_mk_entry` directly. `_mk_entry` remains for the one case that
doesn't need a loader ('lrms', never gated — see 0008's migration
docstring) and for test 6, which deliberately bypasses the loaders to
prove the trigger rejects a direct write.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from backend.domain.closed_set_loaders import load_lgd_derived_entries, load_schema_derived_entries
from backend.domain.closed_sets import ClosedSetTypeNotFound, get_closed_set
from backend.domain.config_versions import (
    as_response,
    get_effective_config,
    get_pinned_config,
)
from backend.models.entities import ClosedSetEntry, ConfigVersion
from landconfigclient import ConfigClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
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
                text(
                    "SELECT 1 FROM pg_trigger WHERE tgname = 'closed_set_entry_enforce_loader_provenance'"
                )
            ).scalar()
            if not exists:
                pytest.skip(f"{TEST_DB_URL} has no Phase 5 (0008) migrated schema — run migrations first")
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

    load_schema_derived_entries(
        session, type_="land_use_category", config_version=version_id, codes=["AGRI", "RESI"]
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

    load_lgd_derived_entries(
        session, type_="village_name", config_version=version_id,
        codes=["V001", "V002"], district="sitapur",
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

    load_schema_derived_entries(
        session, type_="tenure_type", config_version=version_id, codes=["BHUMIDHAR", "SIRDAR"]
    )
    # The attack T5.c exists for: source claims "schema", the ground-truth
    # provenance column says "lrms". 'lrms' is never loader-gated (0008's
    # migration docstring) — a direct construction is legitimate here,
    # standing in for e.g. a legacy import that tagged this row lrms.
    session.add(
        _mk_entry(
            type_="tenure_type", config_version=version_id, code="LRMS_LEAKED_CODE",
            provenance="lrms", source="schema",
        )
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
    load_schema_derived_entries(session, type_="season_code", config_version=v1_id, codes=["KHARIF"])
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
    load_schema_derived_entries(session, type_="season_code", config_version=v2_id, codes=["RABI"])
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


# ---------------------------------------------------------------------------
# 6. P5-05-fix — a caller that is NOT the schema loader attempts to insert
#    provenance='schema' directly. Rejected at the write, not filtered at
#    the read.
# ---------------------------------------------------------------------------


def test_direct_insert_of_schema_provenance_outside_the_loader_is_rejected_at_write(session):
    pointer = _mk_config_version("global", "closed_set.forged_provenance_test")
    session.add(pointer)
    session.flush()
    version_id = pointer.id

    # Bypasses backend.domain.closed_set_loaders entirely — exactly the
    # thing T5.c's original test 3 proved the *read* path tolerates
    # (mislabelled .source). This proves the *write* path no longer
    # tolerates it for .provenance: no SET LOCAL app.closed_set_loader_provenance
    # has been issued in this transaction, so 0008's trigger must refuse
    # the INSERT outright — not silently accept it and rely on the read
    # side to filter it back out later.
    session.add(
        ClosedSetEntry(
            type="forged_provenance_test",
            district=None,
            code="FORGED",
            source="schema",
            provenance="schema",
            config_version=version_id,
        )
    )
    with pytest.raises(DBAPIError, match="must be written by its own sanctioned loader"):
        session.flush()
    session.rollback()


def test_lgd_loader_cannot_be_used_to_write_schema_provenance(session):
    """The loaders are hardcoded, not parameterized by provenance — proved
    here by confirming the LGD loader's own GUC (`'lgd'`) does not
    satisfy the trigger for a hand-constructed `'schema'` row added in the
    same transaction."""
    pointer = _mk_config_version("global", "closed_set.cross_loader_test")
    session.add(pointer)
    session.flush()
    version_id = pointer.id

    load_lgd_derived_entries(session, type_="cross_loader_test", config_version=version_id, codes=["V1"])
    session.add(
        ClosedSetEntry(
            type="cross_loader_test", district=None, code="FORGED",
            source="schema", provenance="schema", config_version=version_id,
        )
    )
    with pytest.raises(DBAPIError, match="must be written by its own sanctioned loader"):
        session.flush()
    session.rollback()
