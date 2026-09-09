# ADR-002 — `trace_id` = literal `"{document_id}:{page_id}"`

## Status
Accepted. Closes the `[DESIGN CHOICE]` marker in
`API-Contracts-and-Interfaces.md` §1 without changing anything it already
implies.

## Context
`API-Contracts-and-Interfaces.md` §1 already states: "Every message carries
a `trace_id` equal to the originating `document_id` + `page_id`, so any
four-person subsystem can be debugged from a single log query without
cross-referencing four teams' logs by hand." It's marked `[DESIGN CHOICE]`
because the exact wire format (delimiter, whether it's a struct or a
string) was left open.

## Decision
`trace_id` is the literal string `f"{document_id}:{page_id}"` — a colon
delimiter, both UUIDs rendered in their canonical hyphenated form, no
struct, no additional encoding. `libs/observability/src/observability/tracing.py::trace_id_for`
is the one function that constructs it; every producer calls that function
rather than formatting the string inline.

## Consequences
- A `trace_id` is greppable directly out of any structured log line without
  parsing — `grep "3fa8...:9c11..." *.log` finds every stage's output for
  one page.
- It is also directly reconstructable by any consumer that already has
  `document_id` and `page_id` in hand — no lookup table needed to answer
  "what would this page's trace_id be."
- It is not globally unique across re-triage of the same page under a new
  model version (§04's replay guarantee: a retry reproduces the *same*
  `trace_id`, deliberately — that's what makes replay debuggable against
  the original run's logs). A reprocessing job (FR-CFG-04) that produces a
  genuinely new record version does not get a new `trace_id` for the same
  page; it is distinguished by the new `WorkEnvelope` and `Record.version`
  it carries, not by trace identity.
