# ADR-004 — Content-addressed object storage: key = `sha256/{d[0:2]}/{d[2:4]}/{digest}`

## Status
Accepted. `local_fs` driver implemented and tested; `s3` driver (for MinIO
per `infra/docker-compose.yml`, and S3-compatible prod storage) implemented
against the same port, using a lazily-imported `boto3` so the library does
not hard-depend on it.

## Context
FR-ING-02 requires every uploaded original hashed with SHA-256 and stored
immutably; FR-ING-04 requires a re-uploaded file recognized by digest and
deduplicated rather than reprocessed. Doing digest-based dedupe as a
database lookup ("does a row with this digest already exist?") makes
dedupe a property of whichever service remembers to check, and makes
immutability a policy someone has to enforce rather than something the
storage layer refuses to violate.

## Decision
`libs/storage/` stores every object under a key derived only from its
content: `sha256/{digest[0:2]}/{digest[2:4]}/{digest}`, where `digest` is
the lowercase hex SHA-256 of the object's bytes. `put()`:

1. Hashes the input.
2. If an object already exists at the resulting key, returns the existing
   key immediately — the FR-ING-04 dedupe guarantee falls straight out of
   the key scheme, no lookup table required.
3. Otherwise writes the object and returns the key.

`put()` never accepts a caller-supplied key and there is no `delete()` or
`overwrite()` on the port — an attempt to write different bytes at a key
that already holds different content raises (this cannot normally happen
since the key is derived from the bytes, but it is checked, because a
storage bug that computed the wrong digest for otherwise-identical content
is exactly the kind of failure this scheme exists to make loud, not silent).
The two-level `{d[0:2]}/{d[2:4]}` prefix keeps any one directory from
holding millions of files as ingest volume grows (50,000 pages/day per
node, §07 of the PRD) — both filesystem and S3-compatible backends benefit
from this even though S3 itself doesn't have a real directory-listing cost
problem, because MinIO/S3 console tooling and any future filesystem-backed
dev/test driver do.

## Consequences
- FR-ING-02 immutability is enforceable at the bucket/filesystem-permission
  level (object-lock policy in prod S3-compatible storage; the `local_fs`
  driver refuses to overwrite an existing path) — a storage property, not
  an application-level check someone can forget.
- FR-ING-04 dedupe is a `put()` return value (`created: bool`), not a
  separate query — `services/backend`'s ingest worker does not need its own
  "have I seen this digest" table.
- The stored key never reveals the original filename, upload batch, or
  district — that information lives in `SourceDocument` rows, keyed by the
  content-addressed key. Two batches that happen to contain the same page
  scanned twice converge on one stored object, which is the point.
- Cost: retrieving "every object uploaded by batch X" requires the
  `SourceDocument` table, never a storage-layer listing — this is a
  deliberate trade (storage stays a dumb, content-addressed blob store; all
  provenance and batch bookkeeping is `services/backend`'s job, matching
  FR-PUB-02's "provenance lives with the record, not with the bytes").
