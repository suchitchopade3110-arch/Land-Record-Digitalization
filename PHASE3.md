# Phase 3 (adjudication band) — build report

Owner: Suchit (`services/backend`). Modules M9 (workbench infra), M10
(conflict register), decision engine, correction write path. This file is
the required Phase 3 record: the blocking-state set chosen, the
maker-checker threshold defaults, crop URL TTL, and every place a
fixture-backed fake stands in for Shree, Shruthi, or Tharun — per Rule 0's
stub-worker contract and this repo's own convention (PHASE2.md) of never
letting a stand-in pass as the real thing silently.

## Corrections to the build prompt, made explicit rather than silently applied

The build prompt this phase was commissioned from disagreed with this
repository on several points. Per CLAUDE.md ("if a document and the
actual `git remote -v` disagree, trust `git remote -v` and say so"), none
of these were silently applied — each is a deliberate divergence, recorded
here.

- **Repo identity, again.** The prompt names
  `github.com/suchitchopade3110-arch/sih26018-land-records`. This
  repository is `suchitchopade3110-arch/Land-Record-Digitalization` — the
  same fact PHASE2.md already recorded against a *different* wrong name
  (`sih26018-land-record-system`). Three build prompts, three different
  wrong repo names, one real repo. Nothing was scaffolded elsewhere.
- **Layout, again.** The prompt implies a by-module layout
  (`services/decision`, a standalone "correction write path" module).
  This repo is by-owner (CLAUDE.md); everything in this phase lives inside
  `services/backend`, matching M9/M10/decision/correction all being
  Suchit's scope per `Team-Split-4-Persons.md`.
- **The `routing_outcome` five-value enum is *not* a contract gap.** The
  build prompt's "four things to settle" section claims
  `API-Contracts-and-Interfaces.md` never lists the five values and that
  `Team-Split-4-Persons.md` still describes a three-way signal. Both
  halves of that claim don't survive reading the actual documents
  supplied this session: `API-Contracts-and-Interfaces.md` §3.2's
  `Extraction.routing_outcome` already lists
  `auto_accept|audit_sample|review|conflict|outside_calibrated_regime`,
  and this repository's `contracts/schemas/extraction.schema.json` and
  `backend.models.entities.Extraction`'s CHECK constraint already encode
  exactly that enum (built in an earlier phase, per `git log` on this
  file predating this session). `Team-Split-4-Persons.md`'s Person 4
  section *does* still say "three-way… auto-accept / review /
  conflict-eligible" (line ~132) — that document is the stale one, not the
  contract. Per CLAUDE.md ("if code and the PRD disagree, the PRD wins"),
  and since the contract (priority 3) already matches the architecture
  doc's §15 ("5 outcomes, not 3") ahead of the team-split doc (priority
  4), no `contracts/proposals/` entry was opened for this — there is
  nothing to propose that isn't already merged. `services/backend/src/
  backend/domain/decision.py`'s exhaustive match on the five outcomes
  (P3-01) already existed before this phase's own work started; this
  phase added the dead-letter framing note and P3-02's cluster-alert
  logic on top of it.
- **`hour_into_session`/`ReviewSession` — a real gap, resolved without a
  contracts proposal.** The prompt's second "thing to settle" is correct:
  nothing in `contracts/` anchors where a review session starts.
  `ReviewTask.hour_into_session` (already a contract field,
  `contracts/schemas/review_task.schema.json`) has no source. Rather than
  opening a `contracts/proposals/` entry, this phase adds
  `backend.models.entities.ReviewSession` as a **backend-internal** table
  (migration 0004) — not a `contracts/schemas/*.json` entity — because
  nothing outside `services/backend` reads or writes it: no other team's
  module consumes a review session, the same "internal, no cross-service
  consumer" reasoning `INGESTION_QUEUE` used in Phase 2 (see PHASE2.md).
  If a future phase needs Tharun's per-officer telemetry work (FR-REV-13,
  gated on PRD §11 Q10 per `docs/open-questions.md`) to read session
  boundaries, *that* is the point to raise a `contracts/proposals/` entry
  — not before Q10 is answered, and not for a value nothing outside this
  service reads today.
- **Timing indistinguishability (the prompt's third point) — not fully
  provable in this environment.** T3.a's byte-diff half is tested
  (`test_review_workflow_and_maker_checker.py::
  test_routed_and_audit_tasks_serialize_byte_identical_except_ids_and_timestamps`)
  and passes by construction: `ReviewTaskPublicView` has no
  `source_stream` field, and `strip_review_task_internals`,
  `review_workflow.claim_next`'s claim query, and `list_review_tasks`'
  read query never branch on it or order by it. The *measured-latency*
  half the prompt calls out as "the assertion most likely to be quietly
  skipped" is not built here either — there's no load-test harness in
  this phase, and asserting "no measurable path difference" needs one.
  Named here rather than silently dropped.
- **Pending maker-checker corrections and the learning-loop leak (the
  prompt's fourth point) — resolved as a staging table, not a status
  column.** `Correction` is a frozen contract schema
  (`contracts/schemas/correction.schema.json`) with no `state` or
  confirming-actor field — adding one would be exactly the kind of
  `contracts/` edit CLAUDE.md says to raise, not make. So the choice the
  prompt asks for was already made by the freeze: `PendingCorrection`
  (`backend.models.entities`, migration 0004) is a **separate,
  backend-internal table**. A `submit_correction` call that trips the
  maker-checker threshold (P3-07) writes there, never to `correction`;
  only `confirm_pending_correction` (called by a second, distinct actor)
  writes the real `Correction` row. Tharun's training-store reader only
  ever queries `correction` — an unconfirmed edit is absent from that read
  view by construction, not by a filter every future reader has to
  remember to apply.

## What shipped

| Item | Where |
|---|---|
| P3-01 five-outcome exhaustive match, hard failure on an unrecognised value | `backend.domain.decision.route` (pre-existing before this phase; this phase documented the "no DLQ yet, Phase 5 scope" framing on `UnroutableExtraction` — `landqueue.port` already names DLQ handling as Phase 5) |
| P3-02 `outside_calibrated_regime` cluster dedup → one alert per cluster | `backend.domain.decision._outside_calibrated_regime`, new `OperationalAlert` table (migration 0004). Dedup window: `review_policy.NOVELTY_CLUSTER_ALERT_DEDUP_WINDOW` (6h, config-fixture) |
| P3-03 review task API: fetch-next, submit, skip (+ existing list) | `backend.domain.review_workflow.claim_next/submit/skip`, `backend.api.review_tasks` (`POST /review-tasks/next`, `POST /review-tasks/{id}/submit`, `POST /review-tasks/{id}/skip`). `SELECT … FOR UPDATE SKIP LOCKED`, backed by the partial index `ix_review_task_claimable` (migration 0004) |
| P3-04 crop delivery: signed, short-TTL URL | `landstorage.ObjectStorePort.sign_get` (HMAC default, `landstorage/signing.py`; `S3ObjectStore` overrides with a real presigned URL), `backend.domain.review_workflow.crop_url_for`, `GET /review-tasks/{id}/crop`. TTL: `review_policy.CROP_URL_TTL_SECONDS` (120s). Audit entry records crop identity (extraction id) only — never the URL, never the value |
| P3-05 reason passthrough | Already built (`ReviewTaskPublicView.reason`, `strip_review_task_internals`) — unchanged this phase, confirmed by T3.a's test |
| P3-06 indistinguishability | Already structurally enforced (`ReviewTaskPublicView` has no `source_stream` field) — this phase adds the T3.a byte-diff test and keeps every new query (`claim_next`, `crop_url_for`, `submit`, `skip`) off `source_stream` in predicate, order, and branch |
| P3-07 maker-checker | `backend.domain.correction.submit_correction`/`confirm_pending_correction`, `PendingCorrection` table. Threshold: `review_policy.MAKER_CHECKER_EDIT_DISTANCE_THRESHOLD` (4), field classes: `review_policy.MAKER_CHECKER_FIELD_CLASSES` (`owner_name`, `share_fraction`, `area`, `survey_number`) |
| P3-08 correction write with full provenance | `backend.domain.correction.submit_correction`. `source_page_digest` read from `Page.storage_uri`'s own content-addressed key, never recomputed |
| P3-09 `AuditSample` officer verdict | `backend.domain.audit_sample_verdict.record_officer_verdict`, wired into `review_workflow.submit` |
| P3-10 conflict register: entry, evidence, states, assignee, ageing | `backend.domain.conflict_register` (`assign`, `transition`, `list_conflicts`, `list_aged` — `open_conflict`/`is_publish_blocked` pre-existing), `backend.api.conflicts` (`GET /conflicts`, `GET /conflicts/{id}`, `POST /conflicts/{id}/assign`, `POST /conflicts/{id}/transition`) |
| P3-11 publish block, config-driven blocking-state set | `backend.domain.publication_gate.check_open_conflict` now reads `review_policy.BLOCKING_CONFLICT_STATES` (`open`, `under_enquiry`, `referred`) instead of a hard-coded `!= 'resolved'`. See "What this phase deliberately did not build" for the DB-trigger half |
| P3-12 resolved conflicts rejoin publish path, resolution+author audited | `conflict_register.transition(new_state="resolved", resolution=...)` requires a resolution string and records it (`purpose=resolution`) alongside the actor |
| P3-13 `hour_into_session`/`cluster_id`/`cluster_size` on `ReviewTask` | Columns already existed (contract fields). This phase adds the session anchor (`ReviewSession`, `backend.domain.review_session.touch_session`) that actually computes `hour_into_session`, wired into `claim_next`. `cluster_id`/`cluster_size` remain unwired — see below |

New migration: `infra/migrations/versions/0004_phase3_adjudication.py` —
additive only (`review_session`, `pending_correction`,
`operational_alert` tables + one partial index), nothing under
`contracts/` touched.

## Fixture-backed fakes standing in for another person's service

| Stand-in | Real owner | Where | Swaps in when |
|---|---|---|---|
| `novelty_cluster_id` as an explicit `route()` kwarg, falling back to the extraction's own id (singleton cluster) | Tharun, novelty clustering | `backend.domain.decision.route`/`_outside_calibrated_regime` | Tharun's novelty engine actually emits a cluster handle — at which point this becomes a real field the Confidence/Novelty→Decision queue message carries in its `payload` (the envelope's `payload` is generic JSON per §1, so this doesn't itself need a `contracts/` change; only a genuinely new persisted field on `Extraction` would) |
| `review_policy.py`'s fixtures (maker-checker threshold/field classes, crop TTL, novelty dedup window, blocking-state set, session gap) | Config service (M13, Phase 5) | `backend.domain.review_policy` | Phase 5's config service serves real `ConfigVersion` rows for these keys — same swap `backend.domain.unit_table` is already waiting on |
| `actor` passed explicitly on every P3 endpoint (`Body`/`Query`), never resolved from a session | Auth/RBAC (FR-SEC-01) | `backend.api.review_tasks`, `backend.api.conflicts` | FR-SEC-01 ships (`backend.api.auth.require_role` is still `NotImplementedError`) — this is a pre-existing P0 gap this phase did not attempt to close, same posture `backend.domain.rescan.assign` already takes |

Everything else this phase touches (the review workflow write path,
maker-checker, the conflict register workflow, crop signing) is Suchit's
own scope end to end — no stand-in needed.

## What this phase deliberately did not build

- **The DB-trigger half of P3-11's "belt and braces."** The advisory-lock
  + CHECK-constraint pattern this repo uses everywhere else needs a write
  path to guard — a `Record.status -> 'published'` transition, concretely.
  That write path doesn't exist yet: `backend.domain.decision._auto_accept`
  already documents that whole-record publish orchestration is Phase 4
  scope, and this phase didn't build it either (it's explicitly out of
  scope per the build prompt). `publication_gate.attempt_publish` is the
  one real gate today — it's exercised by API-shaped callers
  (`test_conflict_register_workflow.py`) but there is only one call site
  in the whole codebase right now, not three, so T3.b's "three entry
  points — API, reprocessing job, adapter push" is descoped to the one
  that exists. Add the trigger the moment Phase 4 adds the write path it
  guards — not before, since a trigger on a column nothing writes yet
  can't be tested honestly.
- **Timing-indistinguishability load test.** See the correction above.
- **`cluster_id`/`cluster_size` wiring on `ReviewTask`.** The columns
  exist (they're contract fields, populated by whoever eventually builds
  crop clustering — explicitly P1/out of scope per the build prompt's own
  "do not build" list) but nothing in this phase writes them; only
  `hour_into_session` is wired, because that's the one FR-REV-16 P3-13
  actually asked this phase to make computable now.
- **T3.f review latency (p95 < 300ms against a seeded 10k-task queue) as
  a CI-run benchmark.** `ix_review_task_claimable` is the throughput-side
  answer (`claim_next`'s query is `WHERE assignee IS NULL AND closed_at
  IS NULL ORDER BY opened_at LIMIT 1 FOR UPDATE SKIP LOCKED`, backed by a
  partial index on exactly that predicate — no full-table scan, no N+1 on
  `reason`/provenance since neither is a join), but no seeded-10k-task
  benchmark or CI wiring for it was built this phase.
- **PostgreSQL `LEVENSHTEIN()`.** `backend.domain.correction.edit_distance`
  is a plain-Python DP implementation rather than the `fuzzystrmatch`
  extension function, so the maker-checker threshold check needs no new
  Postgres extension enabled in `infra/docker-compose.yml` — field values
  here are short (names, survey numbers), so the O(len·len) cost is
  negligible.

## Test suite (T3)

| Suite | File |
|---|---|
| T3.a Indistinguishability (byte-diff half; see correction above for the timing half) | `test_review_workflow_and_maker_checker.py::test_routed_and_audit_tasks_serialize_byte_identical_except_ids_and_timestamps` |
| T3.b Publish block (one entry point; see "what this phase did not build") | `test_conflict_register_workflow.py::test_publish_block_names_the_conflict_id_and_rule`, `test_referred_blocks_publication_the_config_driven_way` |
| T3.c Maker-checker | `test_review_workflow_and_maker_checker.py::test_high_edit_distance_owner_name_correction_lands_in_pending_not_correction`, `test_same_actor_cannot_confirm_their_own_pending_correction`, `test_a_distinct_second_actor_confirms_and_only_then_a_correction_row_exists` |
| T3.d Correction fidelity | `test_review_workflow_and_maker_checker.py::test_source_page_digest_is_copied_from_the_page_record_not_recomputed` (Tharun's exclusion-list round-trip is Tharun's own code — not built or fakeable honestly here) |
| T3.e Five-outcome routing + cluster dedup + dead-letter | `test_triage_and_decision_routing.py::test_unrecognised_routing_outcome_raises_never_defaults_to_review`, `test_outside_calibrated_regime_cluster_of_n_raises_one_alert_not_n_tasks`, `test_two_different_clusters_raise_two_separate_alerts` (plus the five pre-existing per-outcome tests) |
| T3.f Review latency | not built this phase — see above |

Plus: `test_conflict_register_workflow.py` (P3-10/12 workflow),
`libs/storage/tests/test_signing.py` (P3-04's HMAC signer), and
`services/backend/tests/unit/test_main.py`'s route-mount list extended
with every new P3 endpoint.

Verified locally against a real Postgres 16 (this sandbox's
`postgresql-16` package, not `infra/docker-compose.yml`'s container,
which this environment can't run — no docker daemon available here):
`infra/migrations` upgrades `0001→0004` cleanly, and
`services/backend/tests/contract` (71), `services/backend/tests/unit`
(13), `tests/contract` (5), `tests/invariant` (16), and
`libs/storage/tests` (23) all pass — 128 tests, `ruff check` clean on
every file this phase touched (pre-existing lint findings in files this
phase didn't edit were left alone).
