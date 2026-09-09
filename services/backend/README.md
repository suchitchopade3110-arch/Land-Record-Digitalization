# services/backend — owner: Suchit

Owns the API layer, Storage, Queue infrastructure, and: M1 (Ingest & custody),
M2 (triage *routing*, not classifiers), M9 (review workbench infra), M10
(conflict register workflow/storage), M12 + M12b (publication, provenance,
audit log), M13 (configuration management), M14 (dashboard aggregation/API),
M15 (access control & masking).

This is the architectural skeleton every other service's code runs inside —
queue-backed, idempotent workers, and the work envelope every downstream
stage reads from instead of a live registry lookup.

## Consumes
- `INGESTION_QUEUE` (self) → `TRIAGE_QUEUE` (self + Tharun's classifiers)
- `LEARNING_LOOP_QUEUE` is fed BY this service (review workbench emits
  `Correction` on officer submit) — Tharun's modelwork consumes it.

## Publishes
- `TRIAGE_QUEUE` → `TEXT_QUEUE` / `MAP_QUEUE`: fully classified `Page` + pinned `WorkEnvelope`
- `DECISION_QUEUE` → `REVIEW_QUEUE` / `CONFLICT_QUEUE` / `PUBLICATION_QUEUE`
- `REVIEW_QUEUE` → `LEARNING_LOOP_QUEUE`: `Correction`

## Owns (sync APIs)
- `GET /config/{scope}/{key}` — contracts/openapi/config-service.suchit.yaml
- `GET /closed-sets/{type}` — contracts/openapi/closed-sets.suchit-shruthi.yaml (joint w/ Shruthi)

## Calls (sync)
- `GET /models/{module}/active` — Tharun's model registry, to pin the work envelope at triage

## P0 build list

Copied from Team-Split-4-Persons.md §Person 1 — do not let this drift from the source doc:

| Item | FR ref | Status |
|---|---|---|
| Ingest, SHA-256, immutable object store, page split | FR-ING-01–04 | done — Phase 2, `PHASE2.md` |
| Scheduled fixity re-verification | FR-ING-07 | done — Phase 2 |
| Volume completeness (self-index reconstruction, gap alert) | FR-ING-08 | done — Phase 2 |
| Work envelope: pin model_version + config_version at triage | FR-TRI-09 | done — Phase 1, replay-idempotence hardened in Phase 2 |
| RescanTask lifecycle | (M2) | done — Phase 2 |
| Queue-backed, idempotent workers across all stages | §04, §07 |
| Review task queue, crop delivery, keyboard-first UI backend | FR-REV-01–04 |
| Audit-sample tasks visually identical to routed tasks | FR-REV-11 |
| Maker–checker workflow on high-edit-distance corrections | FR-REV-12 |
| Conflict register: entry, evidence attachment, publish-block | FR-CFL-01–03 |
| Versioned records, never overwritten | FR-PUB-01 |
| Provenance on every field | FR-PUB-02 |
| Hash-chained, append-only audit log | FR-PUB-03 |
| External anchoring of chain roots | FR-PUB-08 |
| Sharded audit log | FR-PUB-09 |
| Configuration versioning, author ≠ approver | FR-CFG-01–03 |
| Field-level masking (value, provenance raw_value, crop) | FR-SEC-02 |
| Audit log stores value hashes, not values | FR-SEC-08 |
| RBAC, roles, least privilege | FR-SEC-01 |
| Dashboard: figures derived only from the audit log | FR-ANL-01, FR-ANL-07 |

## Hard invariants this service must never violate
- `ValidationResult.consumed_constraints` non-empty ⇒ `verdict == not_applicable`,
  enforced as a DB constraint/trigger (`infra/migrations`), not a lint warning (FR-VAL-09).
- `ReviewTask.source_stream` must never appear in any API response the officer's
  client can read — enforced at serialization, not the UI (FR-REV-11).
- Field masking (value, provenance raw_value, crop) enforced in three places at
  the serialization layer, never only in the UI (FR-SEC-02).
