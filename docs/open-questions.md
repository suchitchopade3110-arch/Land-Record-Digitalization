# Open questions and what they block

Carried from PRD §11. These are departmental decisions, not engineering
ones — the system is built to degrade gracefully until they're answered,
never to guess at an answer.

| # | Question | What it blocks, concretely, in this repo |
|---|---|---|
| **Q3** | Does the target LRMS expose a documented API, or must the adapter write to a database? | `FR-PUB-04` LRMS/DILRMP/GIS adapters stay mocked (P1 scope, `services/backend`). Overlaps Q9. |
| **Q8** | Who is the external anchoring counterparty for the audit chain? | `FR-PUB-08` (`libs/audit`). P0 ships a daily signed root written to a second store under different credentials — the mechanism is real, the counterparty is not yet named. `libs/audit/anchor.py` is written against an interface (`AnchorWitness`), with a `local_second_store` implementation standing in; do not name a real government-timestamping integration until this is answered. |
| **Q9** | Does the state hold partially digitised RoR data, at what coverage/quality, and can this system get read access to it? | Until resolved: `/closed-sets` never serves LRMS-derived sets (`services/backend/src/backend/api/closed_sets.py` must stay LGD/schema-only — this is enforced, not just documented, see that file's tests); `LegacyRecordRef` stays empty; `FR-VAL-08` (Shruthi's legacy-reconciliation validator) ships as a no-op. |
| **Q10** | Is per-officer and per-writer telemetry acceptable, and under what framing? | `WriterCluster` (Tharun) must stay an unnamed style grouping — no field anywhere in `contracts/schemas/` links a `writer_cluster_id` to a named official. `FR-REV-13`, `FR-LRN-09`, `FR-REV-16` (session-hour tracking, `services/backend`'s review workbench) are framed as calibration of the labelling process, not performance management, and that framing needs department sign-off before any of the three ships to real officers. |
| **Q11** | May a de-identified subset of the corpus be published as a benchmark? | P2 scope (`FR-PUB-06`-adjacent). Not something this repo needs to act on before P1; noted here so nobody assumes silence means yes. |

Two questions worth naming even though they don't block Suchit's P0 scope
directly, because they gate work the other three teammates depend on this
repo's contracts to unblock:

- **Q1** (legal status of an auto-accepted record) changes the entire
  primary-metric shape in PRD §02 and is upstream of every dashboard figure
  `services/backend`'s `M14` aggregation produces. Not a code change today,
  but flag if asked to hard-code a "requires signature" assumption anywhere
  — that assumption is not yet made.
- **Q5** (may correction crops be retained indefinitely) affects
  `libs/storage`'s retention policy for `Correction.crop_uri` objects and
  `FR-SEC-09`. P0 ships without a retention clock on correction crops;
  do not add an indefinite-retention *default* that would need to be walked
  back if Q5 resolves restrictively — leave retention unset/manual until
  answered, per the PRD's own note that FR-SEC-09 "is written to
  accommodate either answer."
