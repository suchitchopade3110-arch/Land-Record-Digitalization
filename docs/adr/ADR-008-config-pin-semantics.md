# ADR-008 — Configuration Pinning Semantics in WorkEnvelope (M13/FR-TRI-09/FR-CFG-02)

## Status
Accepted (Option A — Snapshot Manifest selected by team). Implementation scheduled for T1-06.

## Context
During M2 triage routing (FR-TRI-09), every page receives an immutable `WorkEnvelope` pinned to specific model versions and an active `config_version`. Downstream services (Extraction, Validation, Model Work, Backend) consume this envelope and execute pipeline hops reading configuration settings (e.g. OCR thresholds, confidence bands, novelty bounds, review sample fractions, validation rule tolerances) pinned to that page's envelope.

### The Architectural Gap
1. **Single String Constraint**: In the frozen `contracts/schemas/work_envelope.schema.json`, `config_version` is defined as a single string:
   ```json
   "config_version": { "type": "string" }
   ```
2. **Granular Entity Model**: In PostgreSQL (`entities.py`) and `contracts/schemas/config_version.schema.json`, each `ConfigVersion` row stores a single `(scope, key)` pair and is identified by its own primary key `id`.
3. **Current Lookup Behavior**: `backend.domain.config_versions.get_pinned_config(session, scope, key, config_version)` performs `session.get(ConfigVersion, config_version)` and requires `row.scope == scope and row.key == key`.
4. **The Failure Mode**: If `WorkEnvelope.config_version` stores the row ID of a single key (e.g., `system:ocr_thresholds`), any subsequent pinned read for a different key (e.g., `system:review_policy`) raises `ConfigNotFound`.
5. **Replay Invariant**: Per FR-TRI-09 and CLAUDE.md Invariant 3, replaying a pipeline run months later must resolve the exact configuration values active when the envelope was pinned, even after dozens of config updates have been committed.

This ADR explores two candidate architectural patterns to resolve multi-key pinned configuration under the frozen contract constraint.

---

## Proposed Options

### Option A — Snapshot Manifest (Content-Addressed Configuration Sets)

#### Architecture
Backend introduces an internal, append-only metadata table:
```sql
CREATE TABLE config_snapshot (
    id VARCHAR(64) PRIMARY KEY, -- e.g., "snap-" || sha256_hex
    manifest JSONB NOT NULL,    -- {"scope:key": "config_version_row_id", ...}
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```
1. **Pinning at Triage**:
   - Triage queries all currently effective `ConfigVersion` rows across all scopes and keys (`get_effective_config`).
   - It builds a canonical mapping: `manifest = {f"{row.scope}:{row.key}": row.id for row in effective_rows}`.
   - Computes a deterministic content hash: `snapshot_id = "snap-" + sha256(canonical_json(manifest))[:32]`.
   - `INSERT ... ON CONFLICT (id) DO NOTHING` into `config_snapshot`.
   - Pins `WorkEnvelope.config_version = snapshot_id`.
2. **Resolution on Pinned Read (`GET /config/{scope}/{key}?config_version=snap-...`)**:
   - Backend loads `config_snapshot` by `snapshot_id`.
   - Resolves `target_row_id = snapshot.manifest.get(f"{scope}:{key}")`.
   - Loads and returns the immutable `ConfigVersion` row with `id = target_row_id`.

---

### Option B — As-Of Point-in-Time Sequence Pinning

#### Architecture
Backend assigns a monotonic global sequence number `seq BIGSERIAL` to `ConfigVersion` writes.
1. **Pinning at Triage**:
   - Triage reads the current high-water sequence mark `max(seq)` and current timestamp `pinned_at = now()`.
   - Pins `WorkEnvelope.config_version = f"asof:{max_seq}:{int(pinned_at.timestamp())}"` (or stores `asof_seq` in a synthetic string format matching `config_version: string`).
2. **Resolution on Pinned Read (`GET /config/{scope}/{key}?config_version=asof:seq:ts`)**:
   - Backend parses `max_seq` and `as_of_ts`.
   - Executes temporal point-in-time lookup:
     ```sql
     SELECT * FROM config_version
     WHERE scope = :scope AND key = :key
       AND seq <= :max_seq
       AND effective_from <= :as_of_ts
     ORDER BY effective_from DESC, seq DESC
     LIMIT 1;
     ```
3. **Temporal Invariants**:
   - **Back-dated writes**: Must be strictly forbidden by `write_config_version`. `effective_from` cannot be in the past (`effective_from >= now()`), otherwise past pins would resolve newly inserted rows on replay.
   - **Forward-dated writes**: If a row has `effective_from > as_of_ts`, it is naturally excluded by `effective_from <= :as_of_ts`, preserving the state as it was when pinned.

---

## Detailed Evaluation & Scoring Matrix

| Evaluation Criteria | Option A: Snapshot Manifest | Option B: As-Of Point-in-Time Pin |
| :--- | :--- | :--- |
| **1. FR-TRI-09 Replay Determinism** | **Strong (5/5)**: Exact row IDs are permanently frozen in the manifest JSON. Replays are mathematically immutable regardless of subsequent writes, deletions, or clock shifts. | **Moderate (3.5/5)**: Fully deterministic only if sequence ordering and historical `ConfigVersion` rows are strictly append-only and back-dated inserts are completely disallowed. |
| **2. FR-CFG-04/05 Reprocessing & Impact Preview** | **Strong (5/5)**: Trivial to instantiate a synthetic snapshot manifest for impact preview or historical reprocessing simulations. | **Moderate (3/5)**: Reprocessing with an arbitrary historical configuration set requires finding corresponding temporal timestamps. |
| **3. Contract Freeze (`contracts/`)** | **Perfect (5/5)**: `envelope.config_version` remains an opaque string (`snap-xxxx`). No schema edits to `work_envelope.schema.json` or `config_version.schema.json`. | **Good (4/5)**: `envelope.config_version` is an opaque formatted string (`asof:123:1757570000`). No schema edit needed. |
| **4. `landconfigclient` Caching & Events** | **Strong (5/5)**: Cache key `(scope, key, config_version)` points to a permanent, immutable row ID under that snapshot. Cache invalidation on new config only invalidates unpinned reads. | **Moderate (3/5)**: Cache key `(scope, key, asof:...)` requires parsing or caching query results rather than direct row entity mapping. |
| **5. District-Scoped Keys (`district:<id>`)** | **Strong (5/5)**: Hierarchical fallback (`district:sitapur:thresholds` -> `system:thresholds`) is evaluated once at triage and explicitly baked into the manifest, eliminating runtime fallback ambiguity during pipeline hops. | **Moderate (3.5/5)**: Fallback logic (`district` -> `system`) must be re-evaluated dynamically on every downstream pinned read. |
| **6. Teammate Client Impact** | **Zero Impact (5/5)**: Teammates (`services/extraction`, `services/modelwork`, `services/validation`) continue calling `client.get(scope, key, envelope.config_version)`. Zero changes required. | **Zero Impact (5/5)**: Teammates continue calling `client.get(scope, key, envelope.config_version)`. Zero changes required. |
| **7. P0 Schedule & Complexity Risk** | **Low (4.5/5)**: Single internal table `config_snapshot`, simple dictionary resolution in `get_pinned_config`, zero sequence synchronization overhead. | **Moderate (3/5)**: Requires database migration adding `seq`, temporal index tuning, monotonic sequence locks, and strict validation against back-dated timestamps. |

---

## Sketches of Replay Determinism Verification Tests

### Option A Replay Test Sketch
1. **Setup**:
   - Write `ConfigVersion` rows for `(system, ocr_thresholds) = v1` and `(system, review_policy) = v1`.
   - Triage processes Page `P1` -> Pins `WorkEnvelope` with `config_version = snap-1` (manifest mapping both keys to `v1`).
2. **Configuration Evolution**:
   - Write new `ConfigVersion` rows: `(system, ocr_thresholds) = v2` and `(system, review_policy) = v2`.
   - Confirm unpinned reads return `v2`.
3. **Replay & Assertion**:
   - Replay triage message for Page `P1` or perform pinned reads across Extraction, Validation, and Model Work using `P1`'s envelope (`snap-1`).
   - Assert `GET /config/system/ocr_thresholds?config_version=snap-1` returns `v1`.
   - Assert `GET /config/system/review_policy?config_version=snap-1` returns `v1`.
   - Assert byte-identical execution and zero `ConfigNotFound` errors.

### Option B Replay Test Sketch
1. **Setup**:
   - Write `ConfigVersion` rows at sequence `seq=10` (`ocr_thresholds=v1`) and `seq=11` (`review_policy=v1`).
   - Triage processes Page `P1` -> Pins `WorkEnvelope` with `config_version = asof:11:<t1>`.
2. **Configuration Evolution**:
   - Write `ConfigVersion` rows at sequence `seq=12` (`ocr_thresholds=v2`) and `seq=13` (`review_policy=v2`).
   - Confirm unpinned reads return `v2`.
3. **Replay & Assertion**:
   - Read configuration for Page `P1` using `config_version = asof:11:<t1>`.
   - Assert both `ocr_thresholds` and `review_policy` resolve to `v1` (rows with `seq <= 11`).
   - Assert any write attempt with `effective_from < t1` is rejected with `InvalidEffectiveDateError`.

---

## Recommendation & Decision Question

### Recommendation: **Option A (Snapshot Manifest)**
Option A is strongly recommended for P0 because:
1. It eliminates runtime fallback ambiguities across district vs system scopes by recording the exact row IDs resolved at triage time.
2. It guarantees mathematical replay determinism (FR-TRI-09) independent of database sequence counters or system clock monotonicity.
3. It requires zero contract changes and zero changes to teammate code or `libs/config_client`.
4. It is straightforward to implement in `services/backend` with low P0 schedule risk.
