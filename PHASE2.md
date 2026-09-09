# Phase 2 (acquisition band) — build report

Owner: Suchit (`services/backend`). Modules M1 (ingest/custody) and
M2-routing (triage routing, not the classifiers). This file is the
required Phase 2 record: broker choice, `trace_id` format as implemented,
and every fixture-backed fake standing in for another person's service —
per Rule 0's stub-worker contract and this repo's own convention of never
letting a stand-in pass as the real thing silently.

## A correction to the build prompt, made explicit rather than silently applied

The build prompt this phase was commissioned from assumed a different
repository (`sih26018-land-records` under the same GitHub owner) and
described `trace_id` format and message-broker choice as still-open
`[DESIGN CHOICE]`s for this phase to close. Neither is true of this
repository:

- **Repo identity.** `git remote -v` shows this repo as
  `suchitchopade3110-arch/Land-Record-Digitalization` — CLAUDE.md names
  this exact fact and says to trust the remote over a document that
  disagrees. No second repository was created; nothing was scaffolded
  under `sih26018-land-records`; no import path, module name, or CI
  reference uses that name.
- **Both "open" design choices are already locked**, in `docs/adr/`, and
  Phase 1 already built against them:
  - **Broker: Redis Streams**, `libs/queue/` (ADR-003) — this phase adds
    no new broker code, it reuses the existing `QueuePort`/`landqueue`
    abstraction for everything.
  - **`trace_id` = `f"{document_id}:{page_id}"`** (ADR-002),
    implemented in `libs/observability/src/observability/tracing.py::trace_id_for`.
    This phase's own new code (`backend.domain.triage`,
    `backend.workers.ingestion_consumer`) uses that exact function/format
    for every page-scoped message. The one exception is documented below.

This phase's actual work was implementing P2-01 through P2-13 against
those already-locked decisions, not re-deciding them.

## What shipped

| Item | Where |
|---|---|
| P2-01 Batch ingest (HTTP + drop folder) | `backend.api.documents`, `backend.workers.drop_folder_watcher` |
| P2-02 Custody (streaming SHA-256, content-addressed, dual-copy) | `backend.domain.ingest`, `landstorage.put_stream`/`open_stream` |
| P2-03 Page split | `backend.domain.page_split`, `backend.workers.ingestion_consumer` |
| P2-04 Re-upload recognition | `backend.domain.ingest.ingest_document` (dedupe by digest) |
| P2-05 Batch metadata, district mandatory | `backend.domain.ingest.validate_batch_metadata`, `Batch.scanning_date` |
| P2-06 Fixity sweep, dual credentials | `backend.domain.fixity`, `landstorage.distinct` |
| P2-07 Volume completeness | `backend.domain.completeness` |
| P2-08 Publication gate (reusable) | `backend.domain.publication_gate` — two callers: `check_volume_completeness` (this phase) and `check_open_conflict` (`backend.domain.conflict_register`, Phase 3's gate, wired now) |
| P2-09 Triage router | `backend.domain.triage.lane_for`/`route_page` (already present from Phase 1's build; hardened this phase — see below) |
| P2-10 Work envelope pinning | `landenvelope.pin` (already present; made idempotent this phase — see below) |
| P2-11 Preprocessing hook | `backend.domain.preprocessing` |
| P2-12 RescanTask lifecycle | `backend.domain.rescan` |
| P2-13 Ingest backpressure | `backend.domain.backpressure` (mostly falls out of ADR-005, see below) |
| Demo increment (unit-table refusal) | `backend.domain.unit_table` |

New migration: `infra/migrations/versions/0003_ingest_and_replay_hardening.py`
— additive only (a unique constraint, two CHECK constraints, three
nullable columns), nothing under `contracts/` touched.

## The FR-TRI-09 fix this phase actually needed

Phase 1 had already built `landenvelope.pin.pin()` and
`backend.domain.triage.route_page()`, but `pin()` was a bare INSERT — it
had no "already pinned for this page?" check. That meant a *replayed*
triage message (a redelivery after a worker crash, or the literal T2.d
scenario) would pin a *second* envelope for the same page, with whatever
model versions the registry happened to return at replay time. That is
exactly the bug FR-TRI-09 exists to prevent, and it was latent, not
theoretical — T2.d is what surfaced it.

Fixed by making `pin()` get-or-create by `page_id` (returns the existing
envelope, ignoring the new call's arguments, if one already exists), with
`infra/migrations/0003`'s `uq_work_envelope_page_id` unique constraint as
the DB-level backstop — consistent with this repo's four invariants all
being enforced at the DB layer, not by convention. `route_page` also now
checks for an existing envelope *before* calling `resolve_model_versions`
at all, so a replay doesn't even make the (fixture) registry call, not
just discard its answer. See
`services/backend/tests/contract/test_triage_replay_determinism.py`.

## Fixture-backed fakes standing in for another person's service

Per Rule 0: reads the real interface, emits schema-valid fake output,
zero caller changes needed when the real thing lands.

| Stand-in | Real owner | Where | Swaps in when |
|---|---|---|---|
| `_stub_resolve_model_versions` | Tharun, `GET /models/{module}/active` | `backend.domain.triage` (pre-existing, Phase 1) | Tharun's model registry ships |
| Legibility-threshold breach event | Tharun's legibility scorer | `backend.domain.rescan.open_from_threshold_breach` takes the breach as a direct call, standing in for the event Tharun's scorer would eventually publish | Tharun's scorer + its event ship |
| `index_position` arrival | Shree, page parsing (M3/M4) | `backend.domain.completeness.record_index_position` — a direct call, standing in for a queue message from Shree's extraction lane | Shree's lane + that message contract ship (no such contract exists yet in `contracts/asyncapi/` — deliberately not proposed here, since inventing a cross-service queue contract is exactly the kind of `contracts/` change CLAUDE.md says to raise, not make) |
| District unit-conversion table | Config service (M13, Phase 5) | `backend.domain.unit_table._DISTRICTS_WITH_UNIT_TABLES` | Phase 5's config service serves real per-district tables |
| Config version resolution at triage | Config service (M13, Phase 5) | `backend.config._fetch_from_db` (pre-existing stub, unchanged) | same |

Everything else this phase touches (custody, storage, page split, fixity,
completeness, the publication gate, rescan lifecycle, backpressure) is
Suchit's own scope end to end — no stand-in needed.

## Design decisions this phase made on its own initiative

Two structural additions weren't named field-by-field in the build
prompt but were necessary to build P2-02/P2-03/P2-06 honestly:

- **`landstorage.put_stream`/`open_stream`/`hash_stream_to_spooled_tempfile`.**
  The existing `put()`/`get()` port was bytes-in, bytes-out — fine for a
  correction crop, not for "a 500-page PDF must not sit in memory"
  (P2-02's own wording). Added a streaming path that hashes incrementally
  into a `SpooledTemporaryFile` (RAM up to a threshold, disk beyond it),
  implemented once on the port and specialized per driver
  (`local_fs`/`s3`) only where the destination genuinely differs.
- **`landstorage.distinct` / `get_secondary_store()`.** P2-06 requires
  "two credential sets, two access paths, verified as distinct in config
  validation." Added a second env-var namespace
  (`SECONDARY_OBJECT_STORE_*`) and a config check
  (`assert_distinct_stores`) that compares driver/root/bucket/access-key
  identity and raises if the two would resolve to the same location —
  called at custody time and before every fixity sweep, never assumed.

Neither touches `contracts/` — both are additions to `libs/storage`,
which Suchit solely owns per `infra/CODEOWNERS`.

## `INGESTION_QUEUE` is not a `contracts/asyncapi/` entry, on purpose

`docs/queue-topology.md`'s table (reproduced from
`API-Contracts-and-Interfaces.md` §5) lists "Ingest → Triage" as one hop,
backed by `contracts/asyncapi/triage-queue.yaml`. `INGESTION_QUEUE` is the
backend-internal hop *before* that: `POST /documents` does custody
(hash + store) synchronously and hands off to `backend.workers.
ingestion_consumer` for the (potentially slow, 500-page) page-split work,
via a queue message that never leaves `services/backend`. It carries no
`contracts/schemas/page.schema.json` payload (there's no `Page` yet at
that point — only a document id and a storage key) and no other service
consumes it, so it was not added to `contracts/asyncapi/` — doing so
would be exactly the kind of schema/contract change CLAUDE.md says to
raise as a question, not make, and there was no need to: nothing outside
this service reads that queue. `ingestion_consumer` is the one that
publishes the real, contract-shaped `TRIAGE_QUEUE` message once a page
actually exists.

## P2-13 (backpressure) is mostly free, and that's deliberate

"Ingestion may queue during an outage, it must not reject" falls
straight out of ADR-005's transactional outbox, already built in Phase 1:
`POST /documents` never calls `landqueue` directly — it writes an
`outbox_message` row in the same Postgres transaction as the
`SourceDocument` row, and the relay drains to Redis whenever Redis is
reachable. The request only ever depends on Postgres being up. This
phase's own contribution (`backend.domain.backpressure.check_backlog`) is
a dashboard-facing signal (an undispatched-row-count ceiling), not an
accept/reject gate — the gate the PRD asks for already doesn't exist,
because there was never a decision point where a reject could happen.

## What this phase deliberately did not build

- **Dewarp** (P2-11 names deskew/denoise/binarize/dewarp; only the first
  three are implemented). No dependency-light, off-the-shelf dewarp
  exists at this scope — left as an explicit `TODO: FR-TRI-06 dewarp` in
  `backend.domain.preprocessing`, not a silent no-op under the same name.
- **A generic "run this worker against the queue forever" driver script.**
  Phase 1 didn't build one for `triage_router` either (it's called
  directly from tests/seed); this phase's `ingestion_consumer` and
  `fixity_sweep_scheduler`/`drop_folder_watcher` follow the same level of
  completeness — the domain logic and the `if __name__ == "__main__"`
  polling loops exist; a production process supervisor / consumer-group
  wiring script does not yet, matching what Phase 1 already left open.
- **Auto-repair from the secondary copy on a fixity mismatch.** P2-06
  asks for detection + alert + a genuinely independent second copy, not
  restore automation — `FixityCheck.store` records which copy failed, so
  a restore job has what it needs, but writing that job wasn't in scope
  here and would be scope creep to add unasked.

## Test suite (T2)

All of it lives inside the directories `make verify`/CI already collect —
no new Makefile target was needed:

| Test | File |
|---|---|
| T2.a Acceptance (500-page PDF, digest match, zero-cost re-upload) | `services/backend/tests/contract/test_ingest_custody_and_split.py`, `services/backend/tests/unit/test_page_split.py` |
| T2.b Completeness (gap alert, blocked publish) | `services/backend/tests/contract/test_volume_completeness_and_publication_gate.py` |
| T2.c Fixity (detected on sweep, not retrieval) | `services/backend/tests/contract/test_fixity_sweep.py` |
| T2.d Replay determinism | `services/backend/tests/contract/test_triage_replay_determinism.py` |
| T2.e Fork correctness | `services/backend/tests/contract/test_triage_fork_correctness.py` (extends the pre-existing `test_triage_and_decision_routing.py`) |
| T2.f Envelope completeness (property test) | `tests/property/test_envelope_completeness.py` |

Plus unit coverage with no DB dependency
(`test_page_split.py`, `test_preprocessing.py`, `test_unit_table.py`) and
one more contract file for the smaller P2 items without a named T2 test
(`test_rescan_and_backpressure_and_conflict_gate.py`), and streaming
coverage in `libs/storage/tests/` (`test_local_fs.py`'s new `put_stream`/
`open_stream` cases, `test_distinct.py`).

Verified locally end to end against a real Postgres 16 and Redis 7 (the
same versions `infra/docker-compose.yml` pins), in the exact order
`.github/workflows/contract-tests.yml` runs them: 144 tests pass,
`ruff check` is clean.

One thing worth naming precisely because it was found the hard way: tests
in this phase deliberately call `session.flush()` where earlier patterns
in this repo called `session.commit()`. `tests/e2e`'s outbox relay drains
the *globally* oldest undispatched `outbox_message` rows (across every
queue, `batch_size=100`, no queue filter) — a large-volume test (the
500-page split, or a property test iterating dozens of `route_page`
calls) that commits real rows can push a same-run, later-executing test's
own row past that cap through no fault of its own. Using the test
session's rollback-on-teardown instead of a real commit keeps every new
test's footprint on the shared test database at zero, which is what
avoids this class of order-dependent flake without changing anything
about `Relay`'s (correct, production-appropriate) behavior.
