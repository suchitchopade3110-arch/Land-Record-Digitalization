# services/validation — owner: Shruthi

Owns M6 (six validators) and M7 (entity resolution). Both are "is this
correct / is this unique" logic distinct from extraction and confidence
scoring — but two different mechanisms (pass/fail/not-applicable vs.
blocking-pass union). Don't conflate them internally.

## Consumes
- `VALIDATION_QUEUE` ← canonical `Extraction[]` (from Shree's normalize step)
- `ENTITY_RES_QUEUE` ← canonical `Extraction[]` + `ParcelGeometry` (from Shree's normalize step)

These are genuinely parallel — validation and entity-resolution can proceed
the moment normalize finishes; neither blocks the other.

## Publishes
- `CONFIDENCE_NOVELTY_QUEUE` ← `ValidationResult[]`, dedup match candidates

## Owns (sync API, joint with Suchit)
- `GET /closed-sets/{type}` — schema/LGD-derived only, never LRMS-derived (FR-VAL-09)

## P0 build list

Copied from Team-Split-4-Persons.md §Person 3 — do not let this drift from the source doc:

| Item | FR ref |
|---|---|
| Syntactic validator (formats, ranges, permitted codes) | FR-VAL-01 |
| Arithmetic validator, over-sum/under-sum/unparseable/area-mismatch | FR-VAL-02 |
| Referential validator (LGD codes, khata/survey resolve) | FR-VAL-03 |
| Every validator emits pass/fail/not-applicable + reason + implicated fields | FR-VAL-06 |
| Constraint accounting — consumed constraint forces not-applicable, never pass | FR-VAL-09 |
| Perceptual-hash rescan detection | FR-ENT-01 |
| Multi-pass parcel de-dup (village+survey#, village+khata#, phonetic name+village, geometry centroid) | FR-ENT-02 |
| Never auto-merge people; propose above threshold, hold below | FR-ENT-04 |
| Report duplicate recall, not just precision | FR-ENT-05 |

## Hard invariant this service must never violate
`consumed_constraints` non-empty ⇒ `verdict` MUST be `not_applicable`
(FR-VAL-09) — Suchit enforces this at the DB layer; this service's code
must never try to write `pass` alongside a non-empty `consumed_constraints`.

Under-sum is a register characteristic, not an error — don't force shares
to sum to 1 (FR-VAL-02).
