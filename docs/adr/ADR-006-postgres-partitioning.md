# ADR-006 — Postgres 16 + PostGIS 3.4; `audit_entry` hash-partitioned by `shard_id`; `extraction` range-partitioning designed, deferred to Phase 5

## Status
Accepted for the schema design. `audit_entry` hash-partitioning is
implemented and migrated in Phase 1. PostGIS 3.4 is the target extension
for `ParcelGeometry` (Shree's table, M4) but is **not installed in this
sandbox** — the package could not be fetched from the Ubuntu mirror in this
environment (network fetch failure on an unrelated transitive dependency).
`parcel_geometry`'s `polygon` column is therefore migrated here as `bytea`
with a `-- TODO: PostGIS geometry(Polygon, CRS) once the extension is
available` comment, not as a silent placeholder — flip it to
`geometry(Polygon, 4326)` (or the project's chosen CRS) the moment
`CREATE EXTENSION postgis;` succeeds in the target environment, and it is a
one-line Alembic migration when that happens.

**`extraction`'s monthly range partitioning is deliberately *not* built in
Phase 1**, despite being named in this ADR's title — implementing it turned
out to conflict with a Phase 1 P0 property. Postgres requires a
partitioned table's partition key to be part of every unique/primary-key
constraint on it; `extraction` is referenced by foreign keys from
`review_task`, `audit_sample`, and `correction` on `extraction.id` alone,
and range-partitioning `extraction` by a timestamp would force those three
FKs to either widen to a composite key or be dropped in favor of an
application-level check. Reworking three other tables' foreign keys to buy
a throughput property Phase 1's own gate (the four invariants, schema
conformance, a fake end-to-end run) does not need was judged the wrong
trade under this phase's scope — §5 of the build plan lists "Phase 5 —
Config service, dashboard, **scale hardening**" as the phase this kind of
partitioning belongs to, and that is where it is deferred to, not quietly
dropped. `extraction` ships in Phase 1 as an ordinary (unpartitioned)
table with `district` as an ordinary indexed column, exactly as this ADR
originally specified for that column regardless of the partitioning
question.

## Context
§07 states the throughput target as 50,000 pages/day/node ≈ 1.74 pages/s.
At 40–80 fields/page (PRD §06, `Extraction` is field-level) that's
~70–140 extraction writes/second, and several hundred `AuditEntry` writes
per second once every automated decision and every human action is logged
(FR-PUB-03). A single unpartitioned table serializes on one physical
relation; more importantly for `audit_entry` specifically, ADR-005's
append-only hash chain means every writer needs the *previous* row's hash
to compute the next one — a single global chain makes every write in the
system serialize on one lock, which is a hard throughput ceiling regardless
of how many workers are inserting.

## Decision
- **`audit_entry`** is hash-partitioned by `shard_id` (`PARTITION BY HASH
  (shard_id)`, `MOD 8` at P0 — 8 shards, enough to remove serial contention
  at pilot scale without introducing meaningful shard-rebalance operational
  cost). Each shard maintains its own hash chain (`prev_hash` chains only
  within a shard); a periodic job rolls all shards' latest hashes into one
  global root (FR-PUB-09), which is what gets externally anchored
  (FR-PUB-08, ADR-... — counterparty still open, PRD §11 Q8).
- **`extraction`** is range-partitioned by month on its write timestamp,
  with `district` carried as an ordinary indexed column on every partition
  (not itself a partition key, since a `WHERE district = ...` query without
  a time bound is also a normal access pattern — dashboard aggregation,
  FR-ANL-04). This bounds any one partition's size regardless of total
  ingest volume and lets old partitions be moved to cheaper storage or
  reprocessed (FR-CFG-04) independently of current-month writes.

## Consequences
- `audit_entry`'s hash chain never needs to serialize globally; verifying
  the chain (`libs/audit/verifier.py`) walks each shard independently and
  checks the rolled-up root separately — a tamper in one shard is still
  detectable without re-reading the other seven.
- `extraction` has no partition-maintenance job because it has no
  partitions yet — Phase 5 owns both designing the FK rework this needs
  and building the job that pre-creates each month's partition ahead of
  time. Until then, `extraction`'s size is bounded only by the pilot's
  actual volume, which the Phase 1 gate's fake end-to-end run does not
  come close to stressing.
- `parcel_geometry` ships with a real column, real constraints on
  everything except the geometry type itself, and a two-line migration to
  finish once PostGIS is available — Shree's service does not need to wait
  on this ADR to start writing rows; it only needs to wait on the geometry
  column's final type before spatial queries (ST_*) work.
