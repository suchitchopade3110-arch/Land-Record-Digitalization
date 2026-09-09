# ADR-005 — Transactional outbox in every worker

## Status
Accepted. `libs/outbox/` implemented (writer helper + relay); adoption
inside each service's own DB-writing workers is tracked per-service (see
Phase 1 gate report) rather than assumed complete just because the library
exists.

## Context
Every stage in §04's pipeline both writes to Postgres (the row it produced)
and emits a queue message (telling the next stage to look at it). Those are
two separate systems with no shared transaction. Naively doing them as two
sequential calls has two failure windows: the process crashes after the DB
commit but before the publish (the message is lost — the next stage never
runs, silently), or it crashes after the publish but before the DB commit
(the next stage consumes a message pointing at a row that doesn't exist
yet, or never will). Either one breaks FR-TRI-09's replay-reproducibility
guarantee, because "was this page's triage result actually persisted" and
"was the triage-to-lane message actually sent" stop being the same
question.

## Decision
Every worker that both writes a row and needs to notify the next stage
does so as: **one Postgres transaction** that (a) writes the domain row and
(b) inserts a row into an `outbox_message` table describing the queue
message to send, both under the same `session.commit()`. A separate relay
process (`libs/outbox/relay.py`) polls `outbox_message` for undispatched
rows, publishes each through `libs/queue` (ADR-003), and marks it
dispatched — using the queue's own delivery guarantee, not a second
transaction shared with Postgres, so the relay is itself safe to crash and
resume: an already-dispatched-but-not-yet-marked-dispatched row is
re-published, and every consumer downstream is required to be idempotent
per §04 anyway.

## Consequences
- "Wrote the row, crashed before emitting" becomes impossible to observe:
  the row and the outbox entry commit together, or neither does.
- "Emitted, crashed before commit" becomes impossible by construction: the
  relay is the only thing that ever calls `libs/queue.publish()` for these
  messages, and it only reads already-committed outbox rows.
- The relay can deliver a message more than once (crash between publish and
  mark-dispatched) — this is why every consumer must already be idempotent
  (§04), and is not a new requirement this ADR introduces.
- Cost: one more table per service, one more long-running relay process,
  and a small latency add (poll interval, or a `LISTEN/NOTIFY` trigger to
  avoid polling) between "committed" and "downstream sees it." Accepted,
  because FR-TRI-09's reproducibility guarantee is P0 and this is the
  standard way to get it without two-phase commit across Postgres and the
  broker.
