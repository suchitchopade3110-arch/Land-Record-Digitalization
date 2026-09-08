# services/modelwork — owner: Tharun B L

Owns the classifiers inside M2 (triage), M8 (confidence & novelty engine),
M11 (learning loop), the model cascade, and per-writer adaptation training.
Broadest *conceptual* scope even though it's one module on the diagram —
every place a learned model produces a score that something else acts on.

**Triage's classifiers are ML models, not backend routing** — legibility
scoring, script/language ID, doc-type classification, page-role
classification, and writer clustering all live here, not in services/backend.
Backend owns the queue/routing infrastructure around triage, not the models
that decide where a page goes.

## Consumes
- Raw pages (for triage classifiers), via `TRIAGE_QUEUE`
- `Extraction` + `ValidationResult` (as calibrator features) from Shree and Shruthi, via `CONFIDENCE_NOVELTY_QUEUE`
- `Correction` rows from Suchit's review workbench, via `LEARNING_LOOP_QUEUE`

## Publishes
- `writer_cluster_id`, `legibility_band`, `novelty_score`, `calibrated_confidence`,
  `routing_outcome` — onto `DECISION_QUEUE`, consumed by Suchit's decision engine
  and dashboard; Shree consumes `writer_cluster_id` for adapter selection.

## Owns (sync API)
- `GET /models/{module}/active?stratum={stratum}` — contracts/openapi/model-registry.tharun.yaml
  Callers: Suchit (pin work envelope at triage), Shree (resolve adapter_ref).

## P0 build list

Copied from Team-Split-4-Persons.md §Person 4 — do not let this drift from the source doc:

| Item | FR ref |
|---|---|
| Legibility scoring, script/language ID, doc-type classification, page-role classification | FR-TRI-01–04 |
| Writer-style clustering → writer_cluster_id | FR-TRI-11 |
| Legibility band as a stratification key | FR-TRI-10 |
| Learned calibrator over token confidence, layout certainty, normalization confidence, validator outcomes, field class, script, doc type, print/handwriting, legibility band | FR-CNF-01 |
| Per-stratum calibration (Mondrian conformal preferred), pooled fallback for small strata | FR-CNF-02 |
| Derive auto-accept threshold from configured target error rate | FR-CNF-03 |
| Three-way routing signal into Backend's decision engine | FR-CNF-04 |
| Audit sampling — configurable fraction of auto-accepted fields routed back, per stratum | FR-CNF-07 |
| Page-level escalation when routed-field count on a page exceeds threshold | FR-CNF-08 |
| Record-level error policy alongside field-level | FR-CNF-09 |
| Novelty detection as a distinct routing outcome | FR-CNF-14 |
| Enforced cold-start ramp (Shadow → Ramp → Steady → Regression) | FR-CNF-15 |
| Correction stream tagging (routed / audit / downstream / legacy_digital) | FR-LRN-07 |
| Frozen regression suite evaluated before promotion; blocks on regression or ECE breach | FR-LRN-02 |
| Calibrator refit and promoted jointly with the model | FR-LRN-08 |
| Leakage guard: refuses to run if any source-page digest intersects the exclusion list | FR-LRN-11 |

## Hard invariant this service must never violate
Novelty score is computed FIRST and gates everything else — a page from an
unfamiliar stratum must never get a plausible score from the pooled-
calibrator fallback (FR-CNF-14). High novelty is a separate regime, not
"low confidence."

## Canonical stratum definition (owner: this service; Shruthi uses the
identical definition for FR-ANL-11)
`stratum = field_class × script × print_or_handwriting × legibility_band × writer_cluster_id`
