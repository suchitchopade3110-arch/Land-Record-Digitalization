# ADR-007 — Masking is a serializer-layer policy object applied in exactly one code path

## Status
Accepted. `libs/masking/` implemented; `services/backend`'s serializers
(`services/backend/src/backend/api/serializers.py`) call into it rather
than reimplementing masking logic.

## Context
FR-SEC-02 names the failure mode explicitly: masking one of the three
places a personal-data field appears (the current value, the provenance
`raw_value`, and the linked source crop) while leaving the other two
unmasked is not a partial fix, it's a leak with the same severity as
masking nothing, because an attacker only needs the one copy nobody
thought to mask. If masking logic is written per-route-handler, "no route
can leak" is a claim that has to be re-verified by reading every handler
every time one is added or changed. That does not scale past a handful of
endpoints and it is not a property CI can check.

## Decision
`libs/masking/policy.py` defines one function,
`apply(value, raw_value, crop_uri, *, field_class, role) -> MaskedTriple`,
that is the only place in the codebase allowed to decide whether a
personal-data field is shown or masked. It takes the three copies
together and returns all three consistently masked or all three
unmasked — there is no calling convention that lets a caller mask one and
forget the other two, because there is no function that operates on just
one. `services/backend`'s serializer layer
(`mask_extraction_for_role`, `strip_review_task_internals`) is the only
caller; no route handler in `services/backend/src/backend/api/*.py` is
permitted to call `apply()` directly or implement its own masking check —
handlers call the serializer functions, which call `apply()`.

`ReviewTask.source_stream` (FR-REV-11) is handled by the same principle
even though it isn't personal data: `strip_review_task_internals` is the
one function allowed to decide what a `ReviewTask` looks like to a client,
and it structurally omits the field (via `ReviewTaskPublicView`, a Pydantic
model with no `source_stream` field at all) rather than filtering it out
at serialization time — so there is no dict key to forget to strip.

## Consequences
- "No route can leak a masked field" is testable as one property test
  (`tests/property/test_masking.py`): construct every route's response
  model, assert none of them can carry an unmasked personal-data field for
  a role that shouldn't see it, and assert none of them has a
  `source_stream`-shaped field at all. This is checked once, against the
  policy function and the response models, not once per endpoint.
- Adding a new endpoint that returns `Extraction` data is safe by
  construction as long as it goes through the shared serializer — the
  masking decision is not something the new endpoint's author has to get
  right from scratch.
- FR-SEC-08 (audit log stores value hashes, never values; unmasked reads
  separately logged) is a natural extension of the same single-path
  principle: `libs/audit`'s writer never receives an unmasked value in the
  first place, because the only caller that can produce one
  (`libs/masking.apply(..., role="auditor_unmasked_read")`) logs that call
  itself as the "separately logged purpose-stated operation" FR-SEC-08
  requires, at the point of the call — not as a downstream policy applied
  to the audit log afterward.
- Cost: every place that currently touches an `Extraction` or `ReviewTask`
  on its way to a client must go through `libs/masking` / the serializer
  layer, including future endpoints written by any of the four people —
  this is a real constraint on how `services/backend`'s API surface can
  grow, and it is the point of the ADR, not a side effect of it.
