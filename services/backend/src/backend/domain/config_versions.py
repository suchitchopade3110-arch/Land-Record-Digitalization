"""FR-CFG-01/02/03 — `ConfigVersion` (M13). `contracts/openapi/
config-service.suchit.yaml` §4.1 defines the two read shapes this module
backs: unpinned reads ("what is effective now") and pinned reads ("this
exact version, forever, regardless of what supersedes it").

`write_config_version` (P5-02b) is the write side: a single atomic
two-actor write, not the full draft -> submitted -> approved -> effective
workflow state machine `infra/migrations/versions/
0006_phase5_config_immutability.py`'s docstring describes as still
undesigned. That fuller workflow would need a `workflow_state` column
`contracts/schemas/config_version.schema.json` has no room for — the
schema is `additionalProperties: false` with exactly `id, scope, key,
value, effective_from, author, approver, superseded_by`, and it already
requires both `author` and `approver` on every row, not as a later
transition. `write_config_version` is the write this frozen shape
actually supports: one call names both actors and the row is effective
(once `effective_from` arrives) immediately — there is no intermediate
"proposed, awaiting a second person's separate action" state persisted
anywhere. If a genuine two-HTTP-call maker-checker flow (a distinct
second principal approving someone else's already-submitted draft) is
wanted, that needs a new non-frozen staging table and is a further design
decision, not a mechanical extension of this function — flagged as
P5-02c in `PHASE5.md`'s "Outstanding, carried forward" table rather than
guessed at here. This is a two-person *record* (the row names two
distinct actors), not a two-person *control* (nothing independently
verifies the second name ever took an action) — FR-CFG-03's acceptance
is not fully met by what's built.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from landconfigclient import QUEUE_NAME
from landoutbox import write as outbox_write
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.audit_log import record_config_version_write
from backend.models.entities import ConfigVersion


class ConfigNotFound(Exception):
    """No `ConfigVersion` row satisfies the request — either no version of
    (scope, key) has ever taken effect, or a pinned `config_version` id
    doesn't exist or doesn't belong to the requested (scope, key)."""

    def __init__(self, scope: str, key: str, config_version: str | None = None):
        self.scope = scope
        self.key = key
        self.config_version = config_version
        if config_version:
            msg = f"no config_version={config_version!r} for scope={scope!r}, key={key!r}"
        else:
            msg = f"no effective config for scope={scope!r}, key={key!r}"
        super().__init__(msg)


def get_effective_config(session: Session, scope: str, key: str, *, at: datetime | None = None) -> ConfigVersion:
    """FR-CFG-01: "current" for (scope, key) is the row with the latest
    `effective_from` that is not in the future relative to `at` (defaults
    to now). Only the triage router and the impact preview are allowed to
    call this — everything downstream of triage reads a pinned envelope
    instead (CLAUDE.md invariant 3, FR-CFG-02) via `get_pinned_config`.

    Deliberately does not filter on `superseded_by IS NULL`: the newest
    row whose `effective_from` has passed *is* the effective one by
    construction (P5-01's uniqueness constraint on `(scope, key,
    effective_from)` rules out a tie), and `superseded_by` exists to link
    a superseded row forward for lineage/audit, not to be re-derived here.
    """
    as_of = at or datetime.now(timezone.utc)
    row = session.execute(
        select(ConfigVersion)
        .where(ConfigVersion.scope == scope, ConfigVersion.key == key, ConfigVersion.effective_from <= as_of)
        .order_by(ConfigVersion.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise ConfigNotFound(scope, key)
    return row


def get_pinned_config(session: Session, scope: str, key: str, config_version: str) -> ConfigVersion:
    """The pinned form of §4.1: `?config_version=vN` returns that exact
    row regardless of what is currently effective. Rows are immutable
    (0006's trigger), so a pinned read is stable and cacheable forever —
    see `libs/config_client`'s cache-key contract (P5-04).
    """
    row = session.get(ConfigVersion, config_version)
    if row is None or row.scope != scope or row.key != key:
        raise ConfigNotFound(scope, key, config_version)
    return row


def as_response(row: ConfigVersion) -> dict:
    """Exactly the §4.1 response shape — `key, value, config_version,
    effective_from`. `author`/`approver`/`superseded_by` are internal-only
    and never cross this API; the handler builds the response from this
    function's four fields only, never by serializing the ORM row."""
    return {
        "key": row.key,
        "value": row.value,
        "config_version": row.id,
        "effective_from": row.effective_from.isoformat(),
    }


def build_version_change_event(row: ConfigVersion) -> dict[str, Any]:
    """The invalidation payload `libs/config_client`'s
    `ConfigClient.handle_version_change_event` expects
    (`landconfigclient.subscriber.QUEUE_NAME`) — just enough for a
    subscriber to know which `(scope, key)` to drop from its cache;
    `config_version` rides along for logging only, since a client always
    re-fetches "effective now" on its next read rather than trusting a
    version id carried on the event (P5-04)."""
    return {"scope": row.scope, "key": row.key, "config_version": row.id}


def publish_version_change_event(session: Session, row: ConfigVersion) -> None:
    """Enqueue the invalidation event for `row` via the transactional
    outbox (ADR-005), in the same transaction as the `ConfigVersion`
    INSERT — so "wrote the new version" and "queued the cache
    invalidation" land together, the same guarantee every other
    DB-write-plus-notify path in this repo gets.

    Called by `write_config_version` below (P5-02b) — the live wiring
    P5-04's own phase report flagged as dead code until a write path
    existed to call it.
    """
    outbox_write(session, queue=QUEUE_NAME, envelope=build_version_change_event(row))


class AuthorEqualsApprover(ValueError):
    """FR-CFG-03 — rejected here, at the API/domain layer, before the row
    is even constructed. `ck_config_version_author_ne_approver` (P5-01) is
    the backstop that holds even if this check is bypassed (a raw INSERT,
    a future caller that skips this function) — T(P5-02b).1 asserts both
    layers independently, not just this one."""

    def __init__(self, actor: str):
        self.actor = actor
        super().__init__(f"author and approver must be distinct actors — both were {actor!r}")


def write_config_version(
    session: Session,
    *,
    scope: str,
    key: str,
    value: dict,
    effective_from: datetime,
    author: str,
    approver: str,
) -> ConfigVersion:
    """P5-02b — the one write path for `ConfigVersion` (FR-CFG-01/03).

    - Rejects `author == approver` before touching the database
      (`AuthorEqualsApprover`) — the DB `CHECK` is the backstop, not the
      primary enforcement point.
    - Finds the current head for `(scope, key)` — the row with
      `superseded_by IS NULL`, most recent `effective_from` — and points
      its `superseded_by` at the new row. That is the *only* column
      touched on the prior row (0006's trigger rejects an `UPDATE` of any
      other column on `config_version`); `value`/`effective_from`/
      `author`/`approver` on the prior row are untouched, so "never
      edited in place" (FR-CFG-01) still holds for everything that
      matters about that row's own content.
    - `effective_from` is stored exactly as given — a forward-dated row
      simply isn't picked by `get_effective_config`'s `effective_from <=
      now` filter until that moment arrives; nothing here needs to treat
      "not yet effective" as a special case.
    - Audits both actors (`record_config_version_write`) and publishes
      the cache-invalidation event (`publish_version_change_event`) in
      the same transaction as the two row writes — the caller commits
      once, so all four effects land together or not at all (ADR-005's
      posture, applied here without an outbox for the audit/supersede
      writes since they're ordinary rows in the same transaction, not a
      cross-system call).

    Does not commit — the caller (`api/config_service.py`'s write route)
    commits once every effect above has been staged.
    """
    if author == approver:
        raise AuthorEqualsApprover(author)

    prior_head = session.execute(
        select(ConfigVersion)
        .where(
            ConfigVersion.scope == scope,
            ConfigVersion.key == key,
            ConfigVersion.superseded_by.is_(None),
        )
        .order_by(ConfigVersion.effective_from.desc())
        .limit(1)
    ).scalar_one_or_none()

    new_row = ConfigVersion(
        scope=scope, key=key, value=value, effective_from=effective_from, author=author, approver=approver,
    )
    session.add(new_row)
    session.flush()  # assigns new_row.id without committing

    if prior_head is not None:
        prior_head.superseded_by = new_row.id

    record_config_version_write(
        session, config_version_id=new_row.id, scope=scope, key=key, author=author, approver=approver,
    )
    publish_version_change_event(session, new_row)

    return new_row
