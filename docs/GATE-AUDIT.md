# Gate audit (P5-06 Block 3)

## Status: not performed — the document it audits is not in this repo

The build prompt for this task instructs: "For EVERY named suite in the
plan's phase gate tables — T1.a through T1.f, T2.a through T2.f, T3.a
through T3.f, T4.a through T4.f, T5.a through T5.g — report: does a test
exist, does it assert the property described, is it skipped."

`Backend-Build-Plan-Suchit-5-Phases.md` — the document that names those
suites and describes what each one is supposed to assert — is **not
committed to this repository** and was not attached to this session. A
full repo search (git history included) found no trace of it. Per this
repo's own `CLAUDE.md` ("supplied out of band — ask the user if you need
to re-read it and it is not attached to your session"), auditing against
a phase-gate table means inventing both the table and the pass/fail bar
for each row, which is exactly the kind of silent fabrication CLAUDE.md's
standing rule exists to prevent — worse than skipping the block, since a
fabricated audit would read as authoritative.

**Revision note:** an earlier version of this file (written on a branch
cut from a stale base, before that branch was reset onto the real
`origin/main`) additionally claimed no prior P5-06 session, `page.
processed` event, or dashboard implementation existed anywhere in this
repo's history. That claim was wrong — `origin/main` already carries a
real P5-06 session (`6ecc559`, "P5-06: dashboard aggregation") plus
`PHASE5.md`'s own build report, which this branch now builds on (see
`P5-06-fix:`/`P5-06-units:` in this branch's history for the actual
fixes, applied against that real code rather than reinvented). The
document-not-in-repo finding above is unaffected: `PHASE5.md` and
`CLAUDE.md` both reference "the Phase 1/5 build plan" by name without it
ever being committed — same conclusion as before, for the right reason
this time.

## What this session did instead

Blocks 1, 2, and 4 were implemented as fixes against `origin/main`'s real
P5-06 code (see the `P5-06-fix:`, `P5-06-units:`, and `docs:` commits),
since those blocks describe concrete decisions to make and code to
change, not a comparison against an external table. Block 3 cannot be
done the same way — "does a test exist for suite T3.c" is not answerable
without a document that defines what T3.c is.

## What a real Block 3 needs, if the document is provided later

1. `Backend-Build-Plan-Suchit-5-Phases.md`, attached to the session or
   committed to the repo.
2. For each of T1.a–T5.g: locate the suite by name (grep the plan's own
   table for the file path it names, or infer from the phase/module it
   belongs to), open the actual test file, and check three things
   independently — existence, whether the assertion matches the
   property the plan describes (not just "a test with this name exists
   and passes trivially"), and whether it's marked skip/xfail and why.
3. Special scrutiny on T1.d (idempotency), T2.d (replay determinism), and
   T4.a (tamper detection) specifically: read the test body and state
   plainly whether it would fail if the property under test were broken
   — this repo already has at least three candidates worth checking under
   that bar once the plan is available:
   - `tests/queue/test_conformance.py` — the T1.c driver-conformance
     suite the ADR-003 amendment names explicitly by that ID; likely also
     the home of T1.d's idempotency property (parametrized over both
     drivers plus `InMemoryQueue`), pending plan confirmation.
   - `tests/contract/test_triage_replay_determinism.py` (replay
     determinism — likely T2.d's home, pending plan confirmation).
   - `libs/audit/tests/test_chain.py::test_verify_shard_detects_tampering_even_when_the_tampered_row_recomputes_its_own_hash`
     (tamper detection — likely T4.a's home, pending plan confirmation).

   None of these is a substitute for the audit itself — the plan document
   is the only source that says which file is T1.d/T2.d/T4.a, and whether
   these are even the right tests to point at.
