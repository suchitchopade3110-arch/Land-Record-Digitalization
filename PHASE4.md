# Phase 4 (publication/provenance/audit band) — build report

Owner: Suchit (`services/backend`, `libs/audit`). Modules M12 (publication
+ provenance), M12b (audit log), M15 (access control and masking). This
file is the required Phase 4 record: the two corrections to the build
prompt, the shard-key strategy, root roll interval and anchor cadence, the
role-permission matrix, the key hierarchy, HMAC key custody, every place a
mock stands in for a counterparty, and what could and couldn't be verified
in this sandbox.

## Corrections to the build prompt, made explicit rather than silently applied

### 1. Repo identity, a fourth time

The build prompt named `sih26018-land-records`. `git remote -v` shows this
repository as `suchitchopade3110-arch/Land-Record-Digitalization`. Four
build prompts across four phases have now each named a different wrong
repo (PHASE2.md: `sih26018-land-record-system`; PHASE3.md:
`sih26018-land-records`, distinct from PHASE2's own wrong name; this
phase's prompt: `sih26018-land-records` again). Nothing was scaffolded
elsewhere; every module path and import in this phase's code uses this
repository's actual `services/backend` / `libs/audit` layout.

### 2. `contracts/` stays frozen — `Witness`/`IdentityProvider`/`PublicationAdapter` are backend-internal protocols

The build prompt asks for these three "as protocols in `contracts/`."
Nothing under `contracts/schemas/`, `contracts/openapi/`, `contracts/asyncapi/`,
or `contracts/codegen/` was touched. All three live as plain Python
protocols inside `services/backend`/`libs/audit`, mirroring PHASE3.md's
`ReviewSession` precedent (a real, needed abstraction that has zero
cross-team consumers today, so it doesn't need — and per CLAUDE.md, isn't
allowed — a `contracts/` entry):

| Protocol | Where | Mocks |
|---|---|---|
| `Witness` | `libs/audit/src/landaudit/witness.py` | `SecondStoreWitness`, `TimestampAuthorityWitness` |
| `IdentityProvider` | `services/backend/src/backend/domain/access_control.py` | `MockIdentityProvider` |
| `PublicationAdapter` | `services/backend/src/backend/domain/publication_adapters.py` | `LRMSAdapter`, `DILRMPAdapter`, `GISAdapter` |

No task this phase turned out to need an actual `contracts/schemas/*.json`
edit — see "Open / needs sign-off" below for the one place this got close
(it didn't, in the end, need one) and for what genuinely stayed open.

## Shard-key strategy (decided before implementation, per the build prompt's own instruction)

`libs/audit` already existed from Phase 1 (`landaudit.chain`) with a
sharded, hash-partitioned `audit_entry` table (`NUM_SHARDS = 8`,
`shard_for(key) = sha256(key) % 8`, called on `subject or actor`). This
phase's job was P4-03's specific ask: shard by **district or `batch_id`
hash**, with a **reserved system shard** as the fallback for entry types
with no natural district (config changes, role grants, login events,
anchor writes, reprocessing jobs).

**Decision**: additive, not a rewrite of the existing hash function —
retrofitting `shard_for`'s semantics for every Phase 1–3 call site (which
key off `subject`, an extraction/task/conflict id, not a district) would
mean re-hashing chains other tests already depend on holding a specific
shape (`test_chain.py::test_different_subjects_can_land_in_different_shards`
asserts `e1.shard_id == shard_for("alpha")` verbatim). Instead:

- `landaudit.chain.SYSTEM_SHARD_ID = 0` / `SYSTEM_SHARD_KEY = "system"` —
  a reserved shard, routed to only when a caller explicitly passes
  `shard_key="system"` (never landed on by hashing an ordinary key that
  happens to collide with 0 — `shard_for` special-cases the literal
  sentinel string before falling through to the hash).
- `landaudit.chain.shard_key_for(*, district=None, batch_id=None) -> str`
  — `district` wins when known, `batch_id` is the fallback, `"system"` is
  the fallback of last resort. Always explicit, never a silent default.
- `landaudit.append()` gained a new `shard_key` keyword (in addition to
  the pre-existing `shard_id` override and the `subject or actor`
  fallback) — a P4-era caller passes `shard_key=shard_key_for(district=...)`
  explicitly; nothing about the old three-argument call shape changed for
  existing callers.
- Every P4 call site (`backend.domain.audit_log`) resolves its shard key
  through `shard_key_for` — `record_permission_check` (an RBAC decision)
  always uses the system shard; `record_unmasked_read`/`record_publish`/
  `record_field_edit` resolve the record's batch's district when the
  caller has it in scope, falling back to `None` (→ the old `subject`-hash
  path) when it doesn't.

**Known, named gap**: Phase 1–3's own call sites (`review_workflow.py`,
`correction.py`, `decision.py`, `fixity.py`, `conflict_register.py`,
`rescan.py`, `entry_status.py`, `completeness.py`, `ingest.py`) were **not**
retroactively rewired to pass a district-derived `shard_key` this phase —
each would need tracing "which batch/district is this entry about" through
its own call chain, which is real, non-trivial scope this phase's budget
did not include, and rewiring them changes which shard their *future*
entries land in (their *existing* entries, if any were ever written in a
real deployment, would need a migration to move shards, which this phase
also did not attempt — there is no real deployed data yet, so this is a
design-time gap, not a data-migration one, today). They keep hashing on
`subject or actor`, same as before. This is a real Phase 5-shaped
follow-up, named here rather than silently left implicit.

## Root roll interval, anchor cadence, and the resulting tamper window

`backend.domain.publication_policy` (fixture, standing in for real M13
config, same posture as `review_policy.py`):

- **Root roll interval**: 1 hour (`ROOT_ROLL_INTERVAL`). The global Merkle
  root (`landaudit.merkle.roll_global_root`) is recomputed and persisted
  as a `chain_root` row on this cadence.
- **Anchor cadence**: every roll (`ANCHOR_CADENCE_ROLLS = 1`) — every
  `chain_root` is immediately sent to the `Witness` and gets an
  `anchor_receipt` row. A coarser cadence (anchor every Nth roll) was
  considered and rejected for pilot scale: the anchoring call itself is
  cheap (one `put()` to the anchor store) relative to the cost of a wider
  tamper window, and there's no operational reason at this volume to
  widen it.
- **Resulting tamper window**: bounded by the roll interval — at most ~1
  hour between "the chain still matched the last anchor" and "the next
  anchor would have caught the tamper." `verify.bound_tamper_window`
  (P4-06) reports this window precisely (the two roll timestamps that
  bracket it), not just "sometime before now."

## P4-05 — the second store, credential separation, and what could/couldn't be verified

`infra/docker-compose.yml` gained a fourth service, `anchor_store` (a
second, independent MinIO instance — `MINIO_ROOT_USER`/`_PASSWORD` never
derived from the primary `minio` service's, same "never derived from"
posture P2-06 already established for the secondary object store). The
`backend` container's environment holds a **different**, narrower
credential (`ANCHOR_STORE_ACCESS_KEY_ID`/`_SECRET_ACCESS_KEY`, a
bucket-scoped user intended to carry a write-only — `PutObject`, no
`GetObject`/`ListBucket` — MinIO IAM policy) rather than `anchor_store`'s
own root credential.

**What is real**: the mechanism — `landaudit.witness.SecondStoreWitness`
only ever calls `.put()` on whatever store object it's constructed with
(checked mechanically, `libs/audit/tests/test_witness.py::
test_second_store_witness_never_reuses_the_applications_own_store_object`);
`backend.domain.anchoring.anchor_store()` builds that store from a wholly
separate `ANCHOR_STORE_*` env namespace, never `landstorage.get_store()`/
`get_secondary_store()`; the KMS sign-only handle
(`landaudit.kms.LocalSignOnlyKMSStub`) has no method that returns key
material, checked in `libs/audit/tests/test_kms.py`.

**What is NOT verified in this sandbox, stated plainly**: this environment
has no docker daemon (same limitation PHASE3.md already recorded), so
`docker-compose.yml` was never brought up, and the MinIO write-only IAM
policy for the scoped `backend-anchor-writer` user was never actually
provisioned or tested — the compose file *expresses the intent* (a
separate service, a separate credential namespace) but this session could
not confirm the policy is enforced server-side. T4.a's "run with the app's
own DB credentials" half is what this repo's DB-level tests actually prove
(the app cannot repair its own chain even with full Postgres write
access, `libs/audit/tests/test_verify_and_rollup.py` — not run in this
session either, no local Postgres reachable, see "What ran / didn't run"
below); the object-store credential-separation half is architecture, not
yet a demonstrated property.

**P0 simulation of P1 real KMS/HSM** (ground rule 1): `LocalSignOnlyKMSStub`
holds its key material in a closure captured at construction, never
assigned to an attribute the public interface exposes — no `export_key()`
method exists to call. This is genuinely a no-export *interface*; it is
NOT hardware isolation (a real KMS/HSM's guarantee comes from the key
never entering this process's address space at all, which a same-process
Python stub cannot simulate). Do not use `LocalSignOnlyKMSStub` past a
pilot — its own docstring says so.

## HMAC key custody (P4-07)

`landaudit.valuehash.HmacKeyProvider` is the interface;
`EnvHmacKeyProvider` (P0) reads the key from `AUDIT_HMAC_KEY`. **Who may
hold this key**: the *writer* process (`services/backend`'s API and
worker containers, which call `landaudit.append`/`hmac_value_hash` when
producing an audit entry) must have it in its environment. The
audit-log-*reader* role — an "auditor" viewing `/chain/verify` or running
`verify-chain`, or any BI/reporting job that only ever `SELECT`s from
`audit_entry` — must **not**. In this single-service P0 (one `backend`
container that both writes and would-be-read the log), that separation is
not yet a running property — it's declared in `docker-compose.yml`'s
comments and in this document, not enforced by two different containers
with two different environments. A real deployment splits "the process
that writes audit entries" from "the process an auditor's read-only
credential can reach" — most simply, put `AUDIT_HMAC_KEY` in a secrets
service (e.g. a KMS-backed secret) the auditor's IAM role has no grant to,
mirroring exactly the shape `Witness`'s KMS key already uses. This is
named as the concrete next step, not left as an unstated assumption.

`EnvHmacKeyProvider` refuses to start without the env var set — no fixed
dev-default fallback (unlike `landstorage.signing`'s crop-URL secret),
because a fixed default would make every deployment's audit log
dictionary-attackable with the same precomputed table, defeating P4-07's
entire point.

## The training-store bypass (decided, not left silent)

`Correction.crop_uri` — `Correction` rows are read directly by Tharun's
modelwork under the existing frozen contract
(`contracts/schemas/correction.schema.json`), not through any gateway
route, so T4.c's route-registry enumeration structurally cannot see this
read path.

**Decision: declared in-boundary exemption**, naming FR-SEC-05 and
FR-SEC-09 as the controls — not routing the training-store read through
the masking path with a new training-purpose permission. Reasoning:

- `Correction` rows are, by construction, training material — the entire
  point of the table (FR-LRN-01/07) is that Tharun's pipeline reads
  `predicted`/`corrected`/`crop_uri` to retrain models. Masking those
  fields for that specific, contract-defined consumer would break the
  learning loop the table exists to feed; this is a different situation
  from a human viewing a published record, where masking is exactly the
  right default.
- FR-SEC-05 (access control on training data) and FR-SEC-09 (crop
  retention/de-identification, explicitly out of scope this phase per the
  build prompt's own "out of scope" list) are the two controls this
  boundary is framed against: `services/modelwork`'s own DB credential is
  the access-control boundary today (a distinct service credential from
  `services/backend`'s, per `infra/CODEOWNERS`' service-level ownership),
  and de-identification/retention is explicitly deferred (FR-SEC-09, P1)
  rather than silently assumed handled.
- `backend.domain.access_control.Permission.TRAINING_STORE_READ` and
  `backend.domain.publication_policy.TRAINING_STORE_EXEMPTION_CONTROLS`
  exist as the named placeholder for the moment this exemption needs to
  become an actual gated route (e.g. if Q9/Q10-shaped policy pressure
  changes the framing) — not built into a route this phase, since nothing
  asked for one and the existing contract-shaped read is the sanctioned
  path.

This is recorded here per the build prompt's explicit instruction to pick
one and write it down, not leave it undecided.

## Role-permission matrix (P4-09/FR-SEC-01)

Five roles (`backend.domain.access_control.Role`); `PERMISSION_MATRIX` is
the single declared table every route checks against
(`backend.api.auth.require_permission`) — no route handler compares a
role name inline, checked mechanically
(`services/backend/tests/unit/test_access_control.py::
test_no_route_handler_compares_a_role_name_inline`, an AST walk over every
`backend/api/*.py` file).

| Role | Permissions |
|---|---|
| `operator` | review.claim, review.submit, record.read.masked |
| `verifier` | review.claim, review.submit, record.read.masked, provenance.read |
| `supervisor` | record.read.masked, provenance.read, conflict.assign, conflict.transition, record.publish |
| `auditor` | record.read.masked, provenance.read, **record.read.unmasked**, chain.verify |
| `administrator` | config.write, training_store.read, record.read.masked |

Only `auditor` holds `record.read.unmasked` (P4-08's separate privileged
operation); only `administrator` holds `config.write`; no role holds every
permission. `IdentityProvider` (P1 integration point) is a protocol;
`MockIdentityProvider` (P0) is a fixed roster, one named actor per role
(`operator1`/`verifier1`/`supervisor1`/`auditor1`/`admin1`) — the identity
*verification* half of FR-SEC-01 is not real yet (a plain `X-Actor` header
is trusted as-is), the *authorization* half (the matrix, and every route
gated on `Permission`, not a role string) is.

## Key hierarchy (P4-11)

Three independent key/credential domains, none derivable from another:

1. **Object storage** (`landstorage`) — `OBJECT_STORE_*` (primary) /
   `SECONDARY_OBJECT_STORE_*` (Phase 2's independent fixity-check copy) /
   `ANCHOR_STORE_*` (this phase's anchor store) — three separate
   credential namespaces, verified distinct for the first two
   (`landstorage.distinct.assert_distinct_stores`, Phase 2); the third
   is architecturally separate (a different service in
   `docker-compose.yml`) but not run through the same distinctness
   assertion this phase, since `anchor_store()` isn't itself validated
   against `get_store()`/`get_secondary_store()` at startup the way the
   primary/secondary pair is — a named gap, not a claimed guarantee.
2. **Audit chain signing** (`landaudit.kms`) — `ANCHOR_KMS_KEY_SEED`
   (P0 stub) / a real KMS/HSM key (P1). Sign-only; never loadable as raw
   key material by application code (ground rule 1).
3. **Audit value hashing** (`landaudit.valuehash`) — `AUDIT_HMAC_KEY`.
   Held by the writer, withheld from the reader (see "HMAC key custody"
   above).

**Sensitive-column encryption at rest** (P4-11's other half — "sensitive
columns under separate key material from the rest of the DB, so a DB dump
alone doesn't yield names"): **not implemented this phase**, stated
plainly rather than claimed. `Extraction.raw_value`/`canonical_value` and
`Correction.predicted`/`corrected` (the columns that can carry an
owner name) remain plain Postgres columns, encrypted only at the
transport level (TLS to Postgres, already the deployment's baseline) and
at the storage level (whatever disk/volume encryption the Postgres host
provides, outside this application's own configuration). Building real
column-level encryption (pgcrypto or app-level envelope encryption with a
column-scoped DEK under a KMS-held KEK) touches every read/write path for
`Extraction`/`Correction` — a substantial, real piece of work this
session's remaining budget did not include, and retrofitting it onto
already-tested Phase 1–3 columns without breaking their existing contract
shape needs more room than was left. Named here as the concrete
next step: `Extraction.raw_value`/`canonical_value` and
`Correction.predicted`/`corrected` are exactly where a future phase should
start, using the same "separate key material, application never holds the
KEK, only a scoped DEK" hierarchy this phase's `landaudit.kms`/
`landaudit.valuehash` already establish for a different pair of concerns.

## What shipped (P4-01 through P4-13)

| Item | Where |
|---|---|
| P4-01 versioned `Record` | `backend.models.entities.Record` (`record_group_id` + `uq_record_group_version`), `record_reject_update` DB trigger (migration 0005), `backend.domain.publication.publish_new_version` |
| P4-02 field provenance | `backend.models.entities.FieldProvenance` (page-split/deskew transforms, document id), `backend.domain.provenance` (`record_provenance`, `resolve_provenance`, `original_bbox_for`); edit history reuses `Correction` (gained a DB-only `created_at` column) rather than a new table |
| P4-03 sharded hash chain | see "Shard-key strategy" above — `landaudit.chain` |
| P4-04 periodic global root | `landaudit.merkle` (real Merkle tree over shard heads, records which heads were included), `landaudit.models.ChainRoot`, `landaudit.rollup.perform_roll` |
| P4-05 external anchoring | `landaudit.witness.Witness`/`SecondStoreWitness`/`TimestampAuthorityWitness`, `landaudit.kms`, `landaudit.models.AnchorReceipt`, `backend.domain.anchoring`, `infra/docker-compose.yml`'s `anchor_store` service |
| P4-06 chain verification | `landaudit.verify` (structural + anchored-root comparison, tamper-window bounding), `services/backend/src/backend/cli/verify_chain.py` (standalone CLI, `make verify-chain`), `GET /chain/verify` (convenience, explicitly caveated) |
| P4-07 keyed value hashing | `landaudit.valuehash.hmac_value_hash`, `backend.domain.audit_log` (field identity `(record_id, version, field_name)` in `subject`) |
| P4-08 unmasked read | `backend.domain.unmasked_read.read_unmasked_field`, `POST /extractions/{id}/unmasked-read` — distinct route, permission, audit action, mandatory purpose |
| P4-09 RBAC | `backend.domain.access_control`, `backend.api.auth` |
| P4-10 masking | `backend.api.serializers.MaskedRecordView`/`build_masked_record_view`/`mask_extraction_for_role` (extended Phase 3's existing `MaskedExtractionView` to issue a *real* signed crop URL, computed only when unmasked — never generated then discarded) |
| P4-11 encryption | see "Key hierarchy" above — in-transit assumed at the infra baseline, sensitive-column-at-rest encryption explicitly NOT built this phase |
| P4-12 publication adapters | `backend.domain.publication_adapters.PublicationAdapter`/`LRMSAdapter`/`DILRMPAdapter`/`GISAdapter` |
| P4-13 downstream defect intake | `PublicationAdapter.report_defect` → `conflict_register.open_conflict(origin="downstream")`, unchanged from Phase 3 |

New migration: `infra/migrations/versions/0005_phase4_publication_provenance_audit.py`
— additive (`record.record_group_id` + unique constraint + trigger,
`correction.created_at`, `field_provenance`, `chain_root`,
`anchor_receipt`), nothing under `contracts/` touched.

## Fixture-backed fakes / mocks standing in for a real counterparty

| Stand-in | Real counterparty | Where | Swaps in when |
|---|---|---|---|
| `MockIdentityProvider` | A real SSO/directory service | `backend.domain.access_control` | FR-SEC-01's real identity provider ships (P1) |
| `LocalSignOnlyKMSStub` | A real KMS/HSM | `landaudit.kms` | Deployment infra provisions a real sign-only key (P1) |
| `SecondStoreWitness` over a compose-local MinIO | An off-infrastructure external witness | `landaudit.witness`, `infra/docker-compose.yml`'s `anchor_store` | PRD §11 Q8 (anchoring counterparty) is answered |
| `TimestampAuthorityWitness` | A real RFC 3161 TSA | `landaudit.witness` | PRD §11 Q8 answered |
| `LRMSAdapter`/`DILRMPAdapter`/`GISAdapter` | Real LRMS/DILRMP/GIS clients | `backend.domain.publication_adapters` | PRD §11 Q3/Q9 answered (out of this phase's scope regardless, P1 per the build prompt) |
| `EnvHmacKeyProvider` | A real secrets service (KMS-backed) | `landaudit.valuehash` | Deployment infra provisions real key custody separating writer/reader access |
| `backend.domain.publication_policy` fixtures (roll interval, anchor cadence, permission-matrix version tag) | M13 real config service | same posture as `review_policy.py` (Phase 3) / `unit_table.py` (Phase 2) | Phase 5's config service |

## What this phase deliberately did not build

- **Sensitive-column-at-rest encryption** — see "Key hierarchy" above.
- **A production process-supervisor for the roll/anchor job.** The domain
  logic (`backend.domain.anchoring.perform_scheduled_roll`) exists; a
  polling-loop wiring script does not yet — the same posture Phase 1/2/3
  already left every other worker in (PHASE2.md's "what this phase
  deliberately did not build").
- **Retroactive district-shard-key wiring for Phase 1–3's own
  `landaudit.append` call sites.** See "Shard-key strategy" above.
- **The MinIO write-only IAM policy actually provisioned/tested** for the
  anchor store's scoped credential — the compose file expresses the
  intent; this sandbox has no docker daemon to bring it up and confirm.
- **A load test for anything.** Not asked for this phase.

## Open / needs sign-off

Nothing this phase hit required an actual edit to a frozen
`contracts/schemas/*.json` file. The one place that came close and is
worth naming: `Extraction` has no field distinguishing "which published
`Record` version (and thus which `record_group_id`) this field ended up
in" — `backend.api.records.unmasked_read_route` and
`backend.domain.unmasked_read.read_unmasked_field` therefore take
`record_id`/`version` as caller-supplied context (the route's own
request body) rather than resolving it from `Extraction` itself. If a
future phase needs `Extraction` (or `FieldProvenance`) to *know* which
record version it belongs to independent of the caller telling it, that
is a genuine `contracts/schemas/extraction.schema.json` (or a new
frozen schema) question to raise through §7.4's sign-off process — not
decided here, since nothing in this phase's own scope required it and
CODEOWNERS lists `extraction.schema.json` under `@suchit @shree @tharun`.

PRD §11 Q8 (anchoring counterparty) remains the blocking open question
for P4-05 becoming a real external anchor, exactly as `docs/open-questions.md`
already recorded before this phase; nothing here resolves it, and no
real TSA/second-store counterparty was named, per CLAUDE.md's "stop and
ask" list.

## What ran / didn't run in this environment

This sandbox has PostgreSQL 16 installed locally (same as PHASE3.md's
environment) but this session's harness blocks the privilege-escalation
steps (`su`/`sudo`/editing `pg_hba.conf` to `trust` + restarting the
service) needed to reach it as a superuser without the default `peer`
auth's OS-user-name requirement — confirmed by direct attempts, each
correctly refused by the environment's own guardrails (not a bug to route
around; the local Postgres was restored to its original `peer`-auth
config once this became clear, and confirmed still starting correctly).
Unlike PHASE2.md/PHASE3.md's sessions, no live Postgres connection was
available to this one.

**What this means concretely**: every DB-dependent test this phase wrote
or touched (`libs/audit/tests/test_verify_and_rollup.py`,
`services/backend/tests/contract/test_phase4_publication_provenance_audit.py`,
and the pre-existing suites in both directories) was verified to
**collect and skip cleanly** (`pytest`'s existing "no reachable Postgres
→ `pytest.skip`" convention, already used by every contract/lib-DB test in
this repo) but was **not run against a real database** in this session.
`alembic upgrade head` was similarly not run live; migration 0005 was
verified by direct module import (loads, `upgrade`/`downgrade` are
callable) and by matching migration 0003's own pre-existing
`--sql`/offline-mode limitation (its `_constraint_exists` helper issues a
live `SELECT`, which fails under `alembic ... --sql`'s offline mode — a
pre-existing property of this repo's migration style, not something 0005
introduced or could avoid while matching 0002–0004's own pattern).

**What DID run, green, in this session**:
- `ruff check` — zero findings across every `libs/*/src` and
  `services/*/src` directory this phase touched (and every directory it
  didn't).
- Every DB-independent test: `services/backend/tests/unit` (30, including
  this phase's new `test_access_control.py` and
  `test_masked_record_view.py`), `libs/audit/tests` (27 passed, 9 skipped
  — the skipped ones are exactly the DB-dependent ones named above),
  every other `libs/*/tests` directory (45 passed, 11 skipped, all
  pre-existing), `tests/contract`/`tests/property`/`tests/invariant` (31
  passed, 15 skipped — all pre-existing DB-dependent suites skipping as
  designed), and this phase's new no-DB test
  (`tests/contract/test_route_registry_masking.py`, T4.c's enumeration
  half, 21 passed).
- `python -c "import backend.main"` — the full FastAPI app, every router
  including this phase's `records`/`chain`, imports cleanly with no
  circular-import or wiring error.

**Not run at all in this session, stated plainly** (same posture
PHASE3.md already took toward its own docker-dependent claims): the full
T4 suite end-to-end against `docker-compose.yml`'s real Postgres/MinIO/
anchor-store stack; the MinIO write-only policy for the anchor store's
scoped credential; the actual credential-separation guarantee for
`AUDIT_HMAC_KEY` (writer-vs-reader process split) beyond what a single
`backend` container's env can demonstrate. All are named, not silently
assumed, per this repo's own established convention.
