# Land Record Digitization System (SIH26018)

A queue-backed, asynchronous document platform that converts scanned land
records into verified digital records using OCR/HWR, GIS processing, six
independent-by-construction validators, per-stratum calibrated confidence, a
distinct novelty path for out-of-distribution pages, a self-checking audit
sample, role-scoped masking, and a controlled learning loop that never
trains on the suite it is graded against.

This repository is scaffolding only — see each service's own README for its
P0 build list and current implementation status. Every generated file
carries a `TODO:` comment naming the PRD requirement ID (FR-...) it will
eventually satisfy.

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
- [`libs/observability/`](libs/observability) — shared tracing/logging +
  config-client lib every service imports, so `trace_id` propagation and
  config caching are correct by construction, not by convention.
- [`infra/`](infra) — local dev (`docker-compose.yml`), the single Alembic
  migration history (`migrations/`), and `CODEOWNERS`.
- [`tests/contract/`](tests/contract) — cross-service contract tests run
  against `contracts/` in CI.

## Local dev

```bash
cd infra
docker compose up
```

Each service is reachable directly on its own port (`localhost:8002/docs`
for Shree's extraction service, etc.) and through the gateway on `:8000` —
same route registry, two doors. See `infra/docker-compose.yml` for the full
port map.

## Route registry

See [`gateway/route_registry.yaml`](gateway/route_registry.yaml) for the
live, machine-readable list. Summary:

| Path | Method | Owner | Service |
|---|---|---|---|
| `/config/{scope}/{key}` | GET | Suchit | backend |
| `/models/{module}/active` | GET | Tharun | modelwork |
| `/closed-sets/{type}` | GET | Suchit + Shruthi | backend |
| `/documents` | POST | Suchit | backend |
| `/review-tasks` | GET | Suchit | backend |
| `/conflicts` | GET | Suchit | backend |
| `/dashboard/metrics` | GET | Suchit | backend |

## Hard invariants (non-negotiable, enforced in code/schema/DB, not by convention)

- `ValidationResult.consumed_constraints` non-empty ⇒ `verdict ==
  not_applicable` (FR-VAL-09) — schema `allOf` in
  `contracts/schemas/validation_result.schema.json`, DB trigger in
  `infra/migrations`, enforced by `services/backend`.
- `Extraction.entry_status` defaults to `unknown`, never `live` as a row
  default (FR-EXT-06/07).
- `ReviewTask.source_stream` never appears in a client-facing API response
  (FR-REV-11) — enforced at serialization in `services/backend`.
- Field masking (value, provenance `raw_value`, crop) enforced in three
  places at the serialization layer, never only in the UI (FR-SEC-02).
- Learning-loop leakage guard: a training job refuses to run if any
  source-page digest intersects the frozen regression suite's exclusion
  list (FR-LRN-11) — `services/modelwork/src/modelwork/domain/learning_loop/guard.py`.

## Source documents

- `PRD-Land-Record-Digitization-SIH26018-v0.3.md`
- `Land-Record-System-Canonical-Architecture-v2.md`
- `Team-Split-4-Persons.md`
- `API-Contracts-and-Interfaces.md`
