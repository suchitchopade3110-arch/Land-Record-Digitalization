"""P5-02b — the `ConfigVersion` write path (contract §4.1, FR-CFG-01/03).

Same Postgres fixture convention as `services/backend/tests/contract/
test_config_version_storage_and_read.py`: real Postgres via
`TEST_DATABASE_URL`, skipped if 0006 isn't migrated (this block adds no
new migration — the CHECK and uniqueness constraint it relies on are
already P5-01's). Test 4 additionally needs a real broker (`QUEUE_URL`,
default `redis://localhost:6379/0`) to prove the write path's
outbox -> relay -> subscriber chain for real rather than simulated —
skipped if unavailable, the same posture `libs/outbox/tests/
test_relay.py` already uses for the identical reason.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis as redis_lib
from backend.domain.access_control import Role, default_mock_identity_provider
from backend.domain.config_versions import (
    AuthorEqualsApprover,
    as_response,
    get_effective_config,
    get_pinned_config,
    write_config_version,
)
from backend.models.entities import ConfigVersion
from landaudit.models import AuditEntry
from landconfigclient import ConfigClient
from landconfigclient.subscriber import drain_once as client_drain_once
from landoutbox.relay import Relay
from landqueue.drivers.redis_streams import RedisStreamsQueue
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)
QUEUE_URL = os.environ.get("QUEUE_URL", "redis://localhost:6379/0")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            exists = c.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = 'uq_config_version_scope_key_effective_from'")
            ).scalar()
            if not exists:
                pytest.skip(f"{TEST_DB_URL} has no Phase 5 (0006) migrated schema — run migrations first")
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no migrated schema — run migrations first")
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture
def client(engine):
    """A TestClient wired to `engine` instead of `DATABASE_URL` — overrides
    the one shared `backend.api.deps.get_session` dependency, which now
    covers every route's own session *and* `require_permission`'s audit
    write (P4-09b) — no `DATABASE_URL` alignment needed any more (this
    fixture used to set it directly; see git history / the P4-09b phase
    report for the gap that closed)."""
    from backend.api import deps
    from backend.main import app
    from starlette.testclient import TestClient

    def _override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[deps.get_session] = _override_get_session
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(deps.get_session, None)


def _unique_key(label: str) -> str:
    return f"unit_table.write_workflow_{label}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# 1. A proposed ConfigVersion requires a named approver distinct from the
#    author before it takes effect — rejected at the API, and the DB CHECK
#    still holds as the backstop.
# ---------------------------------------------------------------------------


def test_author_equals_approver_rejected_at_api_and_db_backstop(client, session):
    key = _unique_key("same_actor")

    # API layer: admin1 (the authenticated caller / author) naming itself
    # as approver too.
    resp = client.post(
        f"/config/global/{key}",
        json={
            "value": {"x": 1},
            "effective_from": datetime.now(timezone.utc).isoformat(),
            "approver": "admin1",
        },
        headers={"X-Actor": "admin1"},
    )
    assert resp.status_code == 422
    assert session.execute(select(ConfigVersion).where(ConfigVersion.key == key)).first() is None

    # Domain layer: the same rejection, independent of the HTTP wrapper.
    with pytest.raises(AuthorEqualsApprover):
        write_config_version(
            session, scope="global", key=key, value={}, effective_from=datetime.now(timezone.utc),
            author="admin1", approver="admin1",
        )

    # DB backstop: a raw INSERT that bypasses write_config_version (and
    # the API) entirely still fails, at the CHECK constraint itself —
    # T(P5-02b).1's "assert both layers, not just one."
    with pytest.raises(IntegrityError, match="ck_config_version_author_ne_approver|CheckViolation"):
        session.execute(
            text(
                """
                INSERT INTO config_version (id, scope, key, value, effective_from, author, approver)
                VALUES (:id, 'global', :key, '{}'::jsonb, now(), 'same_person', 'same_person')
                """
            ),
            {"id": str(uuid.uuid4()), "key": key},
        )
    session.rollback()


def test_non_administrator_cannot_write_config(client):
    """`Permission.CONFIG_WRITE` is administrator-only (P4-09's matrix) —
    the write route is gated the same way every other P4 route is, not a
    bespoke check. `verifier1` is a real identity in the default roster
    (confirmed via `default_mock_identity_provider` directly, not just
    inferred from the 403), just not one holding this permission."""
    verifier_identity = default_mock_identity_provider().resolve("verifier1")
    assert Role.VERIFIER in verifier_identity.roles
    assert Role.ADMINISTRATOR not in verifier_identity.roles

    resp = client.post(
        "/config/global/some_key",
        json={"value": {}, "effective_from": datetime.now(timezone.utc).isoformat(), "approver": "admin1"},
        headers={"X-Actor": "verifier1"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. An approved write supersedes the prior version via superseded_by; the
#    prior row is not mutated (FR-CFG-01, never edited in place).
# ---------------------------------------------------------------------------


def test_approved_write_supersedes_prior_version_without_mutating_it(session):
    key = _unique_key("supersede")

    v1 = write_config_version(
        session, scope="district", key=key, value={"bigha_to_sqm": 1000.0},
        effective_from=datetime.now(timezone.utc) - timedelta(days=2),
        author="alice", approver="bob",
    )
    session.commit()
    v1_id = v1.id
    v1_value, v1_effective_from, v1_author, v1_approver = v1.value, v1.effective_from, v1.author, v1.approver

    v2 = write_config_version(
        session, scope="district", key=key, value={"bigha_to_sqm": 1008.0},
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        author="carol", approver="dave",
    )
    session.commit()

    refreshed_v1 = session.get(ConfigVersion, v1_id)
    assert refreshed_v1.superseded_by == v2.id
    # Everything else about v1's own content is untouched.
    assert refreshed_v1.value == v1_value
    assert refreshed_v1.effective_from == v1_effective_from
    assert refreshed_v1.author == v1_author
    assert refreshed_v1.approver == v1_approver


# ---------------------------------------------------------------------------
# 3. effective_from is honoured — a version dated forward is not served by
#    GET /config/{scope}/{key} until it is effective.
# ---------------------------------------------------------------------------


def test_effective_from_is_honoured_forward_dated_row_not_served_yet(session):
    key = _unique_key("effective_from")

    past = write_config_version(
        session, scope="district", key=key, value={"v": 1},
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        author="alice", approver="bob",
    )
    session.commit()

    future = write_config_version(
        session, scope="district", key=key, value={"v": 2},
        effective_from=datetime.now(timezone.utc) + timedelta(days=1),
        author="carol", approver="dave",
    )
    session.commit()

    current = get_effective_config(session, "district", key)
    assert current.id == past.id
    assert current.value == {"v": 1}

    # The pinned form still serves the forward-dated row explicitly, on
    # request — "not effective yet" is not "doesn't exist."
    pinned_future = get_pinned_config(session, "district", key, future.id)
    assert pinned_future.value == {"v": 2}


# ---------------------------------------------------------------------------
# 4. On approval, publish_version_change_event fires and a subscribed
#    ConfigClient returns the new value on its next read — the live wiring
#    of P5-04's event path.
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_available():
    try:
        c = redis_lib.Redis.from_url(QUEUE_URL, decode_responses=True)
        c.ping()
    except redis_lib.RedisError:
        pytest.skip(f"no local Redis at {QUEUE_URL}")


def test_write_fires_invalidation_event_live_through_outbox_relay_and_subscriber(session, engine, redis_available):
    key = _unique_key("live_wiring")

    def _fetch(scope, key_, config_version):
        row = get_effective_config(session, scope, key_) if config_version is None else get_pinned_config(session, scope, key_, config_version)
        return as_response(row)

    client_conf = ConfigClient(fetch=_fetch)

    v1 = write_config_version(
        session, scope="global", key=key, value={"n": 1},
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        author="alice", approver="bob",
    )
    session.commit()

    first = client_conf.get("global", key)
    assert first["config_version"] == v1.id

    v2 = write_config_version(
        session, scope="global", key=key, value={"n": 2},
        effective_from=datetime.now(timezone.utc) - timedelta(seconds=1),
        author="carol", approver="dave",
    )
    session.commit()

    # Still cached — nothing has delivered the event yet.
    assert client_conf.get("global", key)["config_version"] == v1.id

    # Drain the real outbox through a real Redis Streams queue — proves
    # write_config_version's publish_version_change_event call actually
    # queued something, not just that the function was called.
    relay = Relay(sessionmaker(bind=engine), RedisStreamsQueue(url=QUEUE_URL))
    dispatched = relay.drain_once()
    assert dispatched >= 1

    # A subscriber consuming that same queue applies the invalidation to
    # the client — the actual mechanism a real Shree/Shruthi/Tharun
    # process runs continuously. QUEUE_NAME is a shared stream every
    # write_config_version call in this test module publishes to (and a
    # brand-new consumer group starts at the full backlog, per
    # RedisStreamsQueue's own contract) — drain it fully rather than
    # once, so this test's own event isn't missed behind other tests'.
    queue = RedisStreamsQueue(url=QUEUE_URL)
    total_processed = 0
    group = f"test-group-{key}"
    while True:
        processed = client_drain_once(client_conf, queue, group=group, consumer_name="test-consumer", count=100)
        total_processed += processed
        if processed == 0:
            break
    assert total_processed >= 1

    second = client_conf.get("global", key)
    assert second["config_version"] == v2.id
    assert second["value"] == {"n": 2}


# ---------------------------------------------------------------------------
# 5. Approval is recorded in the audit log with both actors.
# ---------------------------------------------------------------------------


def test_approval_recorded_in_audit_log_with_both_actors(session):
    key = _unique_key("audit")

    v1 = write_config_version(
        session, scope="global", key=key, value={"n": 1},
        effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        author="alice", approver="bob",
    )
    session.commit()

    subject = f"global:{key}:{v1.id}"
    entries = session.execute(
        select(AuditEntry).where(AuditEntry.subject == subject).order_by(AuditEntry.action)
    ).scalars().all()

    actions_by_actor = {e.action: e.actor for e in entries}
    assert actions_by_actor == {
        "config_version.approved": "bob",
        "config_version.authored": "alice",
    }
