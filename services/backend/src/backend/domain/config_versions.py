"""FR-CFG-01/02 — read side of `ConfigVersion` (M13). `contracts/openapi/
config-service.suchit.yaml` §4.1 defines the two shapes this module backs:
unpinned reads ("what is effective now") and pinned reads ("this exact
version, forever, regardless of what supersedes it").

Write-side (P5-02's two-person workflow state machine, P5-11's impact
preview) is out of scope for this module — see
`infra/migrations/versions/0006_phase5_config_immutability.py`'s docstring
for what P5-02 still needs and why it isn't guessed at here. Rows are
written directly today (by whatever process runs the two-person approval
today); this module only ever reads — except `build_version_change_event`
/ `publish_version_change_event` below, which exist for P5-02's future
write endpoint to call, not for anything in this module to call itself
(see that function's own docstring for why it is unwired today).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from landconfigclient import QUEUE_NAME
from landoutbox import write as outbox_write
from sqlalchemy import select
from sqlalchemy.orm import Session

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

    Unwired today: P5-02's two-person write workflow (draft -> submitted
    -> approved -> effective) doesn't exist yet as a callable endpoint —
    see `infra/migrations/versions/0006_phase5_config_immutability.py`'s
    docstring, which reports the same gap. `ConfigVersion` rows are
    currently written directly (raw INSERT / test fixtures), not through
    any function in this module, so there is no single call site to wire
    this into without fabricating a write endpoint that P5-02 is meant to
    design properly (the approval semantics, not just the INSERT). This
    function is built and tested (`libs/config_client`'s P5-04 suite)
    against a fake queue so P5-02's endpoint has a one-line call to make
    once it exists: `publish_version_change_event(session, new_row)`
    right after `session.add(new_row)`, before that transaction commits.
    """
    outbox_write(session, queue=QUEUE_NAME, envelope=build_version_change_event(row))
