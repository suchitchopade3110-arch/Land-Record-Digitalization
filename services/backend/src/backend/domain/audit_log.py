"""FR-PUB-03/08/09/FR-SEC-08 -- the backend-side wrapper around
`landaudit`: every call site in `services/backend` that needs to write an
audit entry for something P4-specific (an RBAC check, an unmasked read, a
publish, an edit-history entry) goes through one of the functions here
rather than calling `landaudit.append` directly with ad-hoc arguments --
this is where the shard-key strategy (P4-03) and the HMAC key provider
(P4-07) are wired to a concrete choice once, instead of every call site
re-deciding both.

Field identity for a provenance/correction audit entry is
`(record_id, version, field_name)` (P4-07) -- encoded into `subject` as
`f"{record_id}:{version}:{field_name}"` so it is inspectable in the log
without a join. `shard_key_for_record` below is the one function that
turns "which record is this about" into a shard key (district, when the
caller has one in scope; the reserved system shard otherwise), called by
every P4 audit site in this module.
"""
from __future__ import annotations

from landaudit import (
    SYSTEM_SHARD_KEY,
    EnvHmacKeyProvider,
    hmac_value_hash,
    shard_key_for,
)
from landaudit import append as chain_append
from sqlalchemy.orm import Session

_hmac_key_provider = EnvHmacKeyProvider()


def set_hmac_key_provider(provider) -> None:
    """Test/deployment hook, same posture as `backend.api.auth.set_identity_provider`."""
    global _hmac_key_provider
    _hmac_key_provider = provider


def hash_value(value: str, *, purpose: str = "audit_value_hash") -> str:
    """The one place `services/backend` turns a plaintext personal-data
    value into what `AuditEntry.value_hash` stores (FR-SEC-08) -- never
    `hashlib.sha256` inline at a call site; see `landaudit.valuehash`'s
    docstring for why a bare hash fails T4.d's dictionary-attack check."""
    return hmac_value_hash(value, key_provider=_hmac_key_provider, purpose=purpose)


def shard_key_for_record(*, district: str | None = None, batch_id: str | None = None) -> str:
    return shard_key_for(district=district, batch_id=batch_id)


def record_page_ingested(session: Session, *, page_id: str, district: str | None = None) -> None:
    """P5-06/FR-ANL-01/FR-ING-03 — one entry per `Page` row created
    (`backend.workers.ingestion_consumer.handle`), the page-granular
    "ingested" event `backend.domain.dashboard`'s figure counts."""
    chain_append(
        session, actor="system:ingest", action="page.ingested", subject=page_id,
        shard_key=shard_key_for_record(district=district) if district else None, district=district,
    )


def record_page_processed(session: Session, *, page_id: str, district: str | None = None) -> None:
    """P5-06-fix/FR-ANL-01 — "processed" means every field extracted from
    the page has reached a terminal decision outcome (`auto_accept`,
    `audit_sample`, `review`, `conflict`, `outside_calibrated_regime` —
    `backend.domain.decision.VALID_ROUTING_OUTCOMES`), not "triage routed
    it." The original P5-06 session fired this from
    `backend.domain.triage.route_page` (envelope pinned + routing
    decision made) — flagged there as an assumption ("left the
    acquisition band with a pinned envelope and a routing decision," the
    PRD's own FR-ANL-01 wording wasn't available in that session) and
    now settled: triage merely enqueues a page for extraction, nothing
    about the page's *content* has been decided at that point, so that
    placement made "processed" run ahead of the real backlog. The call
    site moved to `backend.domain.page_lifecycle.mark_processed_if_terminal`,
    invoked from `backend.domain.decision.route()` after every one of its
    five outcome branches — see that module's docstring for the
    replay-guard (fires exactly once per page) and the terminal-outcome
    check itself. This function's own job is unchanged: append the one
    `page.processed` entry once the caller has confirmed the page is
    actually terminal — it does not itself check anything."""
    chain_append(
        session, actor="system:decision-engine", action="page.processed", subject=page_id,
        shard_key=shard_key_for_record(district=district) if district else None, district=district,
    )


def record_permission_check(session: Session, *, actor: str, permission: str, allowed: bool) -> None:
    """FR-SEC-01 -- every RBAC decision, not just denials, is audited. No
    natural district for an access-control decision -- reserved system
    shard (P4-03)."""
    chain_append(
        session,
        actor=actor,
        action="rbac.checked" if allowed else "rbac.denied",
        subject=permission,
        purpose=None,
        shard_key=SYSTEM_SHARD_KEY,
    )


def record_unmasked_read(
    session: Session, *, actor: str, record_id: str, version: int, field_name: str, purpose: str,
    district: str | None = None,
) -> None:
    """FR-SEC-08/P4-08 -- the separate, separately-logged action type for
    an unmasked read. `purpose` is mandatory at the API layer
    (`backend.api.records.unmasked_read_route`); this function only
    records what's already been validated non-empty, it doesn't itself
    re-validate (single point of truth for that check, kept at the API
    boundary where a 422 is the right response shape)."""
    chain_append(
        session,
        actor=actor,
        action="field.unmasked_read",
        subject=f"{record_id}:{version}:{field_name}",
        purpose=purpose,
        shard_key=shard_key_for_record(district=district) if district else None,
    )


def record_publish(
    session: Session, *, actor: str, record_id: str, version: int, district: str | None = None,
) -> None:
    """P5-06: `district` is now stored as plain queryable metadata
    (`AuditEntry.district`) too, not just hashed into a shard key — this
    is one of the sources `backend.domain.dashboard`'s "pages published,
    by district" figure (FR-ANL-01) reads."""
    chain_append(
        session,
        actor=actor,
        action="record.published",
        subject=f"{record_id}:{version}",
        shard_key=shard_key_for_record(district=district) if district else None,
        district=district,
    )


def record_config_version_write(
    session: Session, *, config_version_id: str, scope: str, key: str, author: str, approver: str,
) -> None:
    """FR-CFG-03/P5-02b -- a new `ConfigVersion` is recorded with both
    actors: one entry naming the author, one naming the approver, both
    against the same subject so a reader can correlate them without
    joining into `config_version` itself (the audit log's own meaning
    must never depend on a join into mutable application state, per
    FR-PUB-03's append-only posture). No natural district for a config
    change -- reserved system shard (P4-03), same as `record_permission_check`.
    """
    subject = f"{scope}:{key}:{config_version_id}"
    chain_append(session, actor=author, action="config_version.authored", subject=subject, shard_key=SYSTEM_SHARD_KEY)
    chain_append(session, actor=approver, action="config_version.approved", subject=subject, shard_key=SYSTEM_SHARD_KEY)


def record_field_edit(
    session: Session, *, actor: str, record_id: str, version: int, field_name: str, reason_code: str,
    old_value: str | None, new_value: str | None, district: str | None = None,
) -> None:
    """P4-02/P4-07 -- full edit history with actors. Value hashes only
    (never the values); field identity `(record_id, version, field_name)`
    encoded in `subject`, `reason_code` in `purpose`."""
    combined = f"{old_value or ''} {new_value or ''}"
    chain_append(
        session,
        actor=actor,
        action="field.edited",
        subject=f"{record_id}:{version}:{field_name}",
        purpose=reason_code,
        value_hash=hash_value(combined, purpose="field_edit_history"),
        shard_key=shard_key_for_record(district=district) if district else None,
    )


def record_dead_lettered(
    session: Session, *, queue: str, message_id: str, delivery_count: int, error_class: str,
) -> None:
    """D1 / FR-PUB-03 — dead-letter audit entry. No personal data values reach this entry."""
    chain_append(
        session,
        actor="system:worker-runner",
        action="queue.dead_lettered",
        subject=f"{queue}:{message_id}",
        purpose=f"delivery_count={delivery_count}:error={error_class}",
        shard_key=SYSTEM_SHARD_KEY,
    )

