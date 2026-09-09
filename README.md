# Land Record Digitization System (SIH26018)

A queue-backed, asynchronous document platform that converts scanned land
records into verified digital records using OCR/HWR, GIS processing, six
independent-by-construction validators, per-stratum calibrated confidence, a
distinct novelty path for out-of-distribution pages, a self-checking audit
sample, role-scoped masking, and a controlled learning loop that never
trains on the suite it is graded against.

**Phase 1 is complete and `make verify` is green** (contracts, storage,
queue port, the four hard invariants enforced as real, failing-write
database properties, and a fake end-to-end run — see `CLAUDE.md` and
`docs/adr/`). **Phase 2 (the acquisition band — batch ingest, custody,
page split, fixity, volume completeness, triage routing) is also
complete** — see `PHASE2.md` for the build report, including the fixture
stand-ins for the classifiers/legibility-scorer/config-service Phase 2's
code calls but doesn't implement. Everything else owned by the other
three teammates (M3/M4 text and map lanes, M6/M7 validators and entity
resolution, M2's classifiers, M8 confidence/novelty, M11 learning loop)
remains scaffolding — see each service's own README for its P0 build list
and current implementation status, and `docs/open-questions.md` for what's
blocked on a departmental answer. Every still-unimplemented file carries a
`TODO:` comment naming the PRD requirement ID (FR-...) it will eventually
satisfy.

## Start here

- **`CLAUDE.md`** — the standing repo rule (`contracts/` is frozen), this
  repo's actual identity and layout (by-owner, not by-module — read this
  before assuming a path), and the four hard invariants.
- **`docs/adr/`** — the seven locked technical decisions (ADR-001 through
  ADR-007), each a one-pager on what was decided and why.
- **`docs/queue-topology.md`** — the full stage-by-stage queue map.
- **`docs/open-questions.md`** — PRD §11 items that block real
  implementations (LRMS/legacy read access, the audit-anchoring
  counterparty, per-officer telemetry framing) and exactly what each one
  currently keeps stubbed.

## Team

| Person | Role | Repo namespace |
|---|---|---|
| Suchit | Backend (API, Storage, Queue, M1/M2-routing/M9-M10-M12-M15) | [`services/backend`](services/backend) |
| Shree | OCR + Map/GIS (M3, M4) | [`services/extraction`](services/extraction) |
| Shruthi | Validation + Entity Resolution (M6, M7) | [`services/validation`](services/validation) |
| Tharun B L | Model Work — triage classifiers, confidence/novelty, learning loop (M2-classifiers, M8, M11) | [`services/modelwork`](services/modelwork) |

Ownership is encoded in folder paths, the route registry, and queue topic
names — never tribal knowledge. See `infra/CODEOWNERS` for the enforced
version of this table.

## Where things live

- [`contracts/`](contracts) — the only source of truth for schemas
  (`contracts/schemas/*.json`), synchronous API contracts
  (`contracts/openapi/*.yaml`), and queue contracts
  (`contracts/asyncapi/*.yaml`). No service defines its own copy; generated
  pydantic models land in `contracts/generated/python/` via
  `contracts/codegen/generate.sh`.
- [`gateway/route_registry.yaml`](gateway/route_registry.yaml) — the answer
  to "where does this endpoint live." Every endpoint gets a row here in the
  same PR that adds it.
- [`services/`](services) — one directory per owner, structurally identical
  (`src/<name>/{main.py, api/, workers/, publishers/, domain/, models/,
  config.py}`, `tests/{unit,contract}/`).
- [`libs/`](libs) — shared infrastructure every service imports, one
  package per concern:
  - [`libs/queue/`](libs/queue) — the broker port (ADR-003): Redis Streams
    (default) and NATS JetStream drivers behind one interface.
  - [`libs/storage/`](libs/storage) — content-addressed object storage
    (ADR-004): `local_fs` and `s3`/MinIO drivers.
  - [`libs/outbox/`](libs/outbox) — the transactional outbox + relay
    (ADR-005): a DB write and a queue emit commit as one transaction.
  - [`libs/envelope/`](libs/envelope) — the `WorkEnvelope` pin/read
    mechanism (FR-TRI-09): pinned once at triage, immutable after, with a
    guard that refuses a "resolve current model/config" call mid-pipeline.
  - [`libs/masking/`](libs/masking) — the single masking code path
    (ADR-007, FR-SEC-02): value, provenance `raw_value`, and crop masked
    together, never independently.
  - [`libs/audit/`](libs/audit) — the hash-chained, sharded audit log
    (FR-PUB-03/08/09, FR-SEC-08): value hashes only, never values.
  - [`libs/observability/`](libs/observability) — shared tracing/logging +
    config-client, so `trace_id` propagation and config caching are correct
    by construction, not by convention.
- [`infra/`](infra) — local dev (`docker-compose.yml`), the single Alembic
  migration history (`migrations/`), and `CODEOWNERS`.
- [`tests/`](tests) — cross-cutting suites, run against the whole repo:
  `tests/contract/` (schema conformance), `tests/invariant/` (the four
  hard invariants, proven against a real database), `tests/property/`
  (masking as a property, not per-endpoint), `tests/e2e/` (a fake
  end-to-end pipeline run).

## Local dev

```bash
make up        # docker compose up -d — postgres+postgis, redis, minio, all 4 services, gateway
make migrate   # alembic upgrade head against $DATABASE_URL
make seed      # one fake batch/document/page/review-task, so there's something to look at
```

Each service is reachable directly on its own port (`localhost:8002/docs`
for Shree's extraction service, etc.) and through the gateway on `:8000` —
same route registry, two doors. See `infra/docker-compose.yml` for the full
port map.

Running the test suite (`make verify`) does not require Docker if you
already have a local Postgres and Redis reachable — see the Makefile's
`DATABASE_URL`/`TEST_DATABASE_URL`/`LIB_TEST_DATABASE_URL`/`QUEUE_URL`
variables and `PSQL_SUPERUSER`/`PSQL_SUPERUSER_PGPASSWORD` for how to point
`make test-db` at a non-docker-compose Postgres instance.

```bash
make verify    # install + lint + fresh test DBs + the full suite — the Phase 1 gate
```

`make verify`'s test databases are intentionally split in two:
`landrecords_test` (Alembic-migrated, used by `tests/invariant/` and
`services/*/tests/contract/`, which need the real schema) and
`landrecords_libtest` (a scratch database each `libs/*` package's own
`create_all`/`drop_all` tests use, kept separate so they can never destroy
the migrated schema out from under a suite that runs after them — see
`libs/audit/tests/test_chain.py`'s module docstring for why that isolation
matters specifically for a hash-partitioned table).

## Route registry

See [`gateway/route_registry.yaml`](gateway/route_registry.yaml) for the
live, machine-readable list. Summary:

| Path | Method | Owner | Service | Status |
|---|---|---|---|---|
| `/config/{scope}/{key}` | GET | Suchit | backend | stub (FR-CFG-01/02, Phase 5) |
| `/models/{module}/active` | GET | Tharun | modelwork | stub |
| `/closed-sets/{type}` | GET | Suchit + Shruthi | backend | stub |
| `/documents` | POST | Suchit | backend | **live** — custody, dedupe, page split, batch metadata (Phase 2, see `PHASE2.md`) |
| `/review-tasks` | GET | Suchit | backend | **live** — masked serialization, real DB |
| `/conflicts` | GET | Suchit | backend | stub (Phase 3) |
| `/dashboard/metrics` | GET | Suchit | backend | stub (Phase 5) |

## Hard invariants (non-negotiable, enforced in code/schema/DB, not by convention)

Each of these has a real, failing-write test against a real Postgres
database in `tests/invariant/` — not a schema-shape assertion.

- **`ValidationResult.consumed_constraints` non-empty ⇒ `verdict ==
  not_applicable`** (FR-VAL-09) — a Postgres `CHECK` constraint on
  `validation_result` (`backend.models.entities.ValidationResult`). A
  violating write raises `IntegrityError`, it does not warn.
- **`Extraction.entry_status` defaults to `unknown`, never `live`**
  (FR-EXT-06/07) — `NOT NULL DEFAULT 'unknown'` at the DB layer;
  `backend.domain.entry_status.mark_live` is the *only* application-level
  path to `live`, and it always writes an audit event in the same
  transaction (checked by a static grep-equivalent test, not just runtime
  behavior).
- **`WorkEnvelope` is immutable after write** (FR-TRI-09) — a DB trigger
  rejects any `UPDATE`, whether it goes through `landenvelope`'s Python API
  (which exposes no update method at all) or raw SQL.
- **`ReviewTask.source_stream` never crosses the API boundary** (FR-REV-11)
  — `ReviewTaskPublicView` structurally omits the field (no key to forget
  to strip); a test byte-diffs a routed and an audit-sample task's
  serialized response to prove they're indistinguishable, not just that
  one field is missing.

Also enforced, one code path each (ADR-007):

- Field masking (value, provenance `raw_value`, crop) applied together,
  never independently — `libs/masking`, called only from
  `services/backend/src/backend/api/serializers.py`.
- Learning-loop leakage guard: a training job refuses to run if any
  source-page digest intersects the frozen regression suite's exclusion
  list (FR-LRN-11) — `services/modelwork/src/modelwork/domain/learning_loop/guard.py`.

## Source documents

Supplied out of band, not committed to this repo (see `CLAUDE.md`):

- `PRD-Land-Record-Digitization-SIH26018-v0.3.md`
- `API-Contracts-and-Interfaces.md`
- `Team-Split-4-Persons.md`
- `Land-Record-System-Canonical-Architecture-v2.md` — not supplied;
  `API-Contracts-and-Interfaces.md` §5 and `docs/queue-topology.md`
  reproduce its queue-topology table, which is what this repo has needed
  from it so far.
