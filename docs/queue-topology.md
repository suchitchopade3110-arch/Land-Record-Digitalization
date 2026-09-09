# Queue topology

Reproduced from `API-Contracts-and-Interfaces.md` §5 (itself sourced from
`Land-Record-System-Canonical-Architecture-v2.md` §7, which is not
committed to this repo — see `CLAUDE.md`). Read left to right as "who
waits on whom." Two genuine forks exist and neither is drawn as a chain:
text/map after triage, and validation/entity-resolution after normalize.

| Stage transition | Producer | Consumer | Queue payload | FR ref |
|---|---|---|---|---|
| Ingest → Triage | Suchit | Suchit + Tharun (classifiers run as part of triage) | `Page` (pre-classification fields only) | FR-ING-03 |
| Triage → Text/Map lane | Suchit (router), reads Tharun's classifier output | Shree | `Page` (fully classified) + pinned work envelope | FR-TRI-05/09 |
| Text lane → Assembly | Shree | Shree (assembly is still Shree's module) | `Extraction[]` for a page | FR-EXT-04 |
| Map lane → Assembly | Shree | Shree | `ParcelGeometry[]` | — |
| Assembly → Normalize | Shree | Shree | `RecordAssembly` + raw `Extraction[]` | FR-NRM-01–07 |
| Normalize → Validation | Shree | Shruthi | canonical `Extraction[]` | FR-VAL-01 |
| Normalize → Entity Resolution | Shree | Shruthi | canonical `Extraction[]` + `ParcelGeometry` | FR-ENT-01 |
| Validation + Entity-Res → Confidence/Novelty | Shruthi | Tharun | `ValidationResult[]`, dedup match candidates | FR-CNF-01 |
| Confidence/Novelty → Decision | Tharun | Suchit | `Extraction` with `calibrated_confidence`, `novelty_score`, `routing_outcome` set | FR-CNF-04 |
| Decision → Review / Conflict / Publish | Suchit | Suchit (internal routing) | — | FR-CNF-04, FR-CFL-01 |
| Review → Learning Loop | Suchit | Tharun | `Correction` | FR-LRN-01 |
| Publication → Downstream defect | Suchit | Suchit → re-enters Conflict | defect report | FR-PUB-07 |

## Queue names, as implemented in `contracts/asyncapi/`

| AsyncAPI doc | Corresponds to |
|---|---|
| `triage-queue.yaml` | Ingest → Triage |
| `text-queue.yaml` / `map-queue.yaml` | Triage → Text/Map lane (the fork) |
| `assembly-queue.yaml` | Text/Map lane → Assembly |
| `normalization-queue.yaml` | Assembly → Normalize |
| `validation-queue.yaml` / `entity-res-queue.yaml` | Normalize → Validation / Entity Resolution (the fork) |
| `confidence-novelty-queue.yaml` | Validation + Entity-Res → Confidence/Novelty |
| `decision-queue.yaml` | Confidence/Novelty → Decision |
| `review-queue.yaml` / `conflict-queue.yaml` / `publication-queue.yaml` | Decision's three (of five) queue-backed outcomes — auto-accept and audit-sample both resolve to the publish or review path without a distinct queue of their own; see `services/backend/src/backend/workers/decision_engine.py` for the five-way routing logic that decides which of these three (or "outside calibrated regime," which does not queue anywhere — it terminates at `RescanTask`/an operational alert) a given `Extraction` lands in |
| `learning-loop-queue.yaml` | Review → Learning Loop |

## Broker

Redis Streams locally and in CI, NATS JetStream in the pilot/prod target —
see `docs/adr/ADR-003-queue-port.md`. Every queue above is a
`libs.queue.QueuePort` consumer group, not a broker-native concept a
worker addresses directly.
