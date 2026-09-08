# services/extraction — owner: Shree

Owns M3 (text lane) and M4 (map lane): everything turning pixels into
candidate field values or geometry.

## Consumes
- `TRIAGE_QUEUE` → fully classified `Page` + pinned work envelope (from Suchit's router)

## Publishes
- `TEXT_QUEUE` → `ASSEMBLY_QUEUE`: `Extraction[]`
- `MAP_QUEUE` → `ASSEMBLY_QUEUE`: `ParcelGeometry[]`
- `ASSEMBLY_QUEUE` → `NORMALIZATION_QUEUE`: `RecordAssembly` + raw `Extraction[]`

## Calls (sync)
- `GET /config/{scope}/{key}` — unit tables, gazetteer closed sets (Suchit)
- `GET /closed-sets/{type}` — gazetteer re-ranking, FR-OCR-07 (Suchit/Shruthi)
- `GET /models/{module}/active?writer_cluster_id=...` — adapter_ref lookup (Tharun)

## P0 build list

Copied from Team-Split-4-Persons.md §Person 2 — do not let this drift from the source doc:

| Item | FR ref |
|---|---|
| Printed OCR, HWR, per-region routing | FR-OCR-01–02 |
| Table structure detection, cell-wise extraction | FR-OCR-03 |
| Bounding box + confidence on every token | FR-OCR-04 |
| Engine identity, model version, config version recorded per extraction | FR-OCR-05 |
| Field extraction against document-type schema | FR-EXT-01 |
| Relationship extraction (owner ↔ share ↔ parcel) | FR-EXT-02 |
| Multi-page record assembly, with rationale recorded | FR-EXT-04 |
| Geometric strikethrough/cancellation detection | FR-EXT-06 |
| Georeference (control points/monuments/sheet corners), RMSE, transform type | FR-MAP-01, FR-MAP-09 |
| Vectorize parcel boundaries | FR-MAP-02 |
| Bind survey-number labels to polygons | FR-MAP-03 |
| Compute polygon area | FR-MAP-04 |

## Hard invariant this service must never violate
`Extraction.entry_status` defaults to `unknown`, set to `live` only after an
explicit pass — never as a row default. (FR-EXT-06/07)

## Tightest coupling in the system
When the joint decoder (FR-EXT-05, P1) or gazetteer re-ranker (FR-OCR-07, P1)
uses a corpus or constraint during decoding, this service must emit which
constraint was consumed (metadata alongside the `Extraction`), so Shruthi's
validator can self-report `not_applicable` (FR-VAL-09). Build a shared test
fixture for it before either side writes much code.
