# CLAUDE.md

This file is instructions to any Claude session (or other coding agent)
working in this repository. It is not a schema, but it governs how the
schemas are changed.

## The standing rule

```
contracts/ is frozen. Any change under contracts/ requires the sign-off listed in
API-Contracts-and-Interfaces.md §7.4 and must be raised as a question, never made
as an edit. If a task appears to require a contract change, stop and report it.
```

This is not advisory. A silent contract edit breaks three other people's
builds and is the single worst failure mode available in this repository.
`infra/CODEOWNERS` enforces the sign-off list mechanically; this paragraph
is why that file exists.

## Repository identity (read before assuming anything about paths)

This repository is `suchitchopade3110-arch/Land-Record-Digitalization` on
GitHub — **not** `sih26018-land-record-system`. An earlier build prompt for
this project named the latter; the actual GitHub remote configured for this
project's sessions is the former. Do not create a second repository, do not
scaffold code outside this one, and do not silently rename things to match
a document — if a document and the actual `git remote -v` disagree, trust
`git remote -v` and say so in your report.

## Layout: by-owner, not by-module

`services/` is organized **by owner**, not by pipeline module:

| Directory | Owner | Covers |
|---|---|---|
| `services/backend` | Suchit | API layer, storage, queue infra, M1 (ingest/custody), M2 (triage *routing*), M9 (review workbench backend), M10 (conflict register), M12/M12b (publication, provenance, audit), M13 (config management), M14 (dashboard aggregation), M15 (access control, masking) |
| `services/extraction` | Shree | M3 (text lane), M4 (map lane) |
| `services/validation` | Shruthi | M6 (six validators), M7 (entity resolution) |
| `services/modelwork` | Tharun | Triage classifiers (inside M2), M8 (confidence/novelty), M11 (learning loop) |

A prior brief for this project specified a by-module layout instead
(`services/ingest`, `services/triage_router`, `services/decision`, …). That
layout was **not** adopted here — the by-owner layout above was already
built and merged before this rule was written, and restructuring it was
judged higher-risk than the by-module brief's own default. Do not
restructure `services/` to match the by-module layout without raising it
first: this is exactly the "do not restructure top-level directories
without saying so explicitly in the phase report" case.

Shared infrastructure other people's code imports lives in `libs/`, one
directory per concern (`libs/queue`, `libs/storage`, `libs/outbox`,
`libs/envelope`, `libs/masking`, `libs/audit`, `libs/observability`) — this
part does follow the by-module brief, because these are genuinely shared
mechanisms, not any one owner's service.

## Source of truth, in priority order

1. `PRD-Land-Record-Digitization-SIH26018-v0.3.md` — 156 requirements. Cite
   FR IDs inline in code comments and commit messages. (Not committed to
   this repo — supplied out of band; ask the user if you need to re-read
   it and it is not attached to your session.)
2. `Land-Record-System-Canonical-Architecture-v2.md` — module boundaries,
   queue topology, the five decision outcomes. (Not committed to this repo
   either; `API-Contracts-and-Interfaces.md` §5 reproduces its queue
   topology table and is the fallback if this document is unavailable.)
3. `API-Contracts-and-Interfaces.md` — **frozen** in the same sense as
   `contracts/`. Schemas, work envelope, the three synchronous APIs, the
   stratum definition.
4. `Team-Split-4-Persons.md` — ownership and P0/P1 boundaries.

If code and the PRD disagree, the PRD wins. If your intuition and the
contract disagree, the contract wins.

## The four invariants (do not weaken these; see `tests/invariant/`)

1. `ValidationResult.consumed_constraints` non-empty ⇒ `verdict =
   'not_applicable'` — enforced as a Postgres CHECK constraint. A violating
   write raises, it does not warn. (FR-VAL-09)
2. `Extraction.entry_status` defaults to `unknown`, never `live` —
   `NOT NULL DEFAULT 'unknown'`, and the *only* application-level path to
   `live` writes an audit event in the same transaction. (FR-EXT-06/07)
3. The `WorkEnvelope` is immutable after write — a DB trigger raises on any
   `UPDATE`. Nothing downstream of triage resolves "current model" or
   "current config"; everything reads the pinned envelope. (FR-TRI-09,
   FR-CFG-02)
4. `ReviewTask.source_stream` never crosses the API boundary —
   `ReviewTaskPublicView` structurally omits the field; a contract test
   diffs a routed and an audit task response byte-for-byte. (FR-REV-11)

## Dashboard: one source of truth, one named exception (M14, FR-ANL-01/07/08)

Every dashboard figure is a query over `audit_entry` (materialized views over
it are fine; a separately incremented counter or an in-memory tally is not —
it can drift from the log it's supposed to summarize, silently).

`FR-ANL-08` (cost per page) is the one place this rule cannot be met as
written: the PRD sources the inference-cost component from observability
data, not from the audit log, because inference cost is not something the
audit log records. This is a single, named exception, not a precedent:

- Only the inference-cost component of `FR-ANL-08` may be served from a
  source other than `audit_entry`. Every other figure on the dashboard,
  `FR-ANL-08`'s own officer-cost component included, still comes from the
  audit log.
- That component is served from a separate, explicitly named path (its own
  query/endpoint, not folded into the general dashboard query surface), and
  the response it produces must declare its source (e.g. a `source:
  "observability"` field or equivalent) so a caller can never mistake it
  for an audit-log-derived figure.
- Do not generalize this into a second general-purpose rollup table, and do
  not let a second figure quietly acquire the same treatment — if another
  figure seems to need a non-audit-log source, that is a new instance of
  "stop and ask," not an extension of this exception.

A reconciliation test (T5.d in the Phase 5 build prompt) checks every other
figure against a direct `audit_entry` query and asserts this exception is
the only one, and that it is labelled as such in the response.

## Stop and ask, do not decide

- A task that appears to need a `contracts/` edit.
- Anything gated on PRD §11 Q3/Q9 (LRMS and legacy read access), Q8
  (anchoring counterparty), Q10 (per-officer telemetry), Q11 (benchmark
  publication).
- A conflict between two source documents.
- Anything that would let a personal-data value reach an audit log, or a
  `source_stream` value reach a client.
- Pressure to cut a feature for time. Raise the timeline problem instead of
  quietly dropping scope.

See `docs/adr/` for the locked technical decisions (ADR-001 through
ADR-007) and `docs/open-questions.md` for what is blocked and on what.
