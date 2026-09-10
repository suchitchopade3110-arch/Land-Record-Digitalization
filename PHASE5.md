# Phase 5 (config service, dashboard, scale hardening) — build report

Owner: Suchit (`services/backend`, `libs/config_client`, `libs/queue`,
`libs/audit`). This file does not retroactively document P5-01/02/03
(`ConfigVersion` storage, immutability, the two-person `CHECK` constraint,
the read API) or the sessions that built them — those merged before this
document existed. It covers the sessions that followed: P5-04, P5-05
(+ its P5-05-fix hardening), P4-09b, the ADR-003 fake/driver parity
amendment, and P5-06 — and records, per this session's own instructions,
one scoping gap that must not be mistaken for done.

## What shipped

| Task | What | FR |
|---|---|---|
| P5-04 | `libs/config_client` (`landconfigclient`) — the shared Config Service client every service's config read goes through: cache on `(scope, key)` for unpinned reads, on the full `(scope, key, config_version)` triple for pinned reads, invalidated by a broker event (never a timer alone), refuses to resolve "current" inside a pinned `WorkEnvelope` scope. | FR-CFG-02, contract §4.1 |
| P5-05 / P5-05-fix | `GET /closed-sets/{type}` corpus-partition rule — real logic, backed by a `closed_set_entry` table whose `provenance` column can only be written by a sanctioned loader (`backend.domain.closed_set_loaders`), enforced by a DB trigger keyed on a per-transaction Postgres session GUC — not just a second untrusted column. | FR-VAL-09, contract §4.3 |
| P5-02b | `POST /config/{scope}/{key}` — the `ConfigVersion` write path. See "Outstanding, carried forward" below for what this does *not* fully close. | FR-CFG-01/03 |
| P4-09b | `backend.api.deps.get_session` — one shared, overridable session dependency; `require_permission`'s audit write goes through it instead of a private `session_factory()` call. | — (testability, not a PRD FR) |
| ADR-003 amendment | Fake/driver parity rule + `tests/queue/test_conformance.py` (T1.c), the driver conformance suite named in the Phase 1 build plan that had never actually been built. `landqueue.testing.InMemoryQueue` is now the one canonical `QueuePort` fake. | — |
| P5-06 | `GET /dashboard/metrics` — pages ingested/processed/published, by district and over time, each a direct query over `audit_entry`. Added `AuditEntry.district` (plain metadata, not hash-chained) and two new audit action types (`page.ingested`, `page.processed`) since neither district nor page-granular "ingested"/"processed" events existed before. | FR-ANL-01/07 |

## Outstanding, carried forward (not built, not mistaken for done)

| ID | Task | FR | Notes |
|---|---|---|---|
| **P5-02c** | Real two-person **control** for `ConfigVersion` writes (a genuine maker-checker: a distinct second principal independently approving a named first principal's already-submitted draft) | FR-CFG-03 | P5-02b's `POST /config/{scope}/{key}` is a two-person **record**, not a two-person **control**: the DB `CHECK (author <> approver)` and the API-layer check both hold — a single call cannot name the same actor as both — but nothing stops the one authenticated caller (the `author`) from typing any name into the `approver` field and pushing the write through alone. The row records two distinct names; no independent action by the second name is ever required or verified. FR-CFG-03's acceptance criterion (a genuine second-person control, not just a distinct-names constraint) is **not fully met** by what's built. A real fix needs a `draft -> submitted -> approved -> effective` workflow state machine backed by a staging table — `contracts/schemas/config_version.schema.json` is `additionalProperties: false` with no `workflow_state` field and already requires both `author` and `approver` on every row, so that state machine cannot live on `ConfigVersion` itself without a `contracts/` edit (frozen, not this task's to make). The staging table is new, non-frozen application storage — a legitimate, buildable P1-scoped follow-up, not a schema change. **Do not build this from a task description alone; it needs its own design pass** (what states, who may transition them, how a rejected draft is distinguished from one still pending) the way P5-02's own two-person *record* got before it was built. |

This entry exists so `POST /config/{scope}/{key}` is never read as "the
two-person rule, done" from the fact that a write endpoint exists and a
CHECK constraint holds. It records the gap; it does not close it.

## Confirmed, not asserted

`make verify` (fresh `landrecords_test` + `landrecords_libtest`, flushed
local Redis, full migration chain through 0009) is green for every
session this report covers — commands recorded in each session's own
final report/commit messages (P4-09b `8c77ed0`, ADR-003 `35c410f`, P5-06
`6ecc559`, plus the P5-05-fix/P5-02b/P4-10b work immediately preceding
this document).
