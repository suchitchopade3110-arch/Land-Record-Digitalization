# ADR-003 — Broker behind a port: `libs/queue/`, Redis Streams + NATS JetStream drivers

## Status
Accepted. Redis Streams driver implemented and tested against a real local
broker; NATS JetStream driver implemented against the same port but not
exercised in CI (no NATS server in this environment) — swap-in only.

## Context
`infra/docker-compose.yml` already picked Redis Streams as the local/CI
broker (documented there as a `[DESIGN CHOICE]`, made in week one per the
brief). §04 of the PRD requires every module to be "a queue-backed worker,
not a function call in a chain," with idempotent handlers and harmless
replay. Locking every caller directly to a Redis client makes a later
production broker swap (NATS JetStream, or Kafka if the department already
runs it) a rewrite of every `services/*/workers/*.py` and
`services/*/publishers/*.py` file instead of a driver swap.

## Decision
`libs/queue/` defines one abstract port (`QueuePort` — `publish`,
`consume` with a named consumer group and per-message ack, `replay_from`
a sequence position) that every service's publishers and workers depend
on, never a broker's native client directly. Two drivers implement the
port:

- `libs/queue/drivers/redis_streams.py` — `XADD`/`XREADGROUP`/`XACK`
  against Redis Streams. Default in local dev and CI (matches
  `infra/docker-compose.yml`'s `redis` service).
- `libs/queue/drivers/nats_jetstream.py` — the same port over NATS
  JetStream, for the pilot/prod target named in the original brief.
  Implemented against the port's contract; not run against a live NATS
  server in this environment, so treat it as reviewed-but-unexercised
  until it is.

The driver is selected by an environment variable
(`QUEUE_DRIVER=redis_streams|nats_jetstream`, defaulting to
`redis_streams`) read once at process start, per `libs/queue/__init__.py`'s
`get_queue()` factory.

Both drivers give consumer groups, per-message ack, redelivery of an
un-acked message, and replay from a recorded sequence position — the three
properties every worker's idempotence depends on (§04, `ADR-005`'s
transactional outbox). A Kafka driver, if ever needed, is a third
implementation of the same port with no caller changes.

## Consequences
- Every `services/*/workers/*.py` file imports `libs.queue.get_queue()`,
  never `redis` or `nats` directly. A `grep -rl "^import redis" services/`
  outside `libs/queue/drivers/` is a review finding.
- The port's `consume()` contract requires callers to ack only after the
  handler completes without raising — this is what makes "crash mid-handle"
  safe to retry, and it's the property `libs/outbox` (ADR-005) is built to
  preserve on the producer side.
- Cost: the port is the lowest common denominator of both driver's
  semantics (e.g. neither exactly-once delivery nor ordered fan-out across
  multiple consumer instances is assumed) — a worker that needs a stronger
  guarantee than "at-least-once, per-consumer-group" needs to say so
  explicitly, not assume the broker under the port provides it.

## Amendment (2026) — fake/driver parity is a rule, not an accident

`landconfigclient.subscriber.drain_once` shipped with `block_ms=0`
documented as "non-blocking," when against the real Redis Streams driver
`block_ms=0` means `XREADGROUP ... BLOCK 0` — Redis's own syntax for
"block indefinitely." The bug went untested-and-green because the only
`QueuePort` fake in the repository at the time (a private `FakeQueue`
inside `libs/config_client/tests/test_subscriber.py`) ignored `block_ms`
entirely — `consume()` returned immediately regardless of the argument.
The fake and the real driver disagreed about a parameter's meaning, and
nothing ever exercised that disagreement.

**Rule:** any `QueuePort` parameter a real driver treats as load-bearing
must be honoured by the fake used in tests — not merely accepted in the
fake's signature. "Load-bearing" means: changing the parameter's value
changes the real driver's observable behaviour. If a fake accepts a
parameter but ignores its value, that is the same defect class as not
accepting it at all, because a test written against the fake proves
nothing about what happens against the real broker.

**Where this is proven, not assumed:** `tests/queue/test_conformance.py`
(T1.c, per §2 of the Phase 1 build plan — "Redis and NATS drivers pass
the identical suite") is the one place this repository asserts driver
behaviour, and it now includes the canonical fake
(`landqueue.testing.InMemoryQueue`) as a third parametrized target
alongside both drivers. A new load-bearing parameter (or a new driver)
is not "done" until it passes through this suite; a fake gaining a new
method without a matching conformance assertion is the mechanical
version of the same silent-drift risk this amendment exists to close.

**Parity audit performed against the fake this amendment replaces**
(`FakeQueue`, now removed — `landqueue.testing.InMemoryQueue` is its
replacement, imported by `libs/config_client/tests/test_subscriber.py`
and exercised directly by T1.c):

| Parameter/behaviour | Old fake | Real drivers | Fixed in `InMemoryQueue`? |
|---|---|---|---|
| `block_ms` | Ignored — never waited, at any value | `0` blocks indefinitely (Redis `BLOCK 0`); `N>0` bounds the wait | Yes — real blocking via `threading.Condition`, both cases covered by T1.c |
| `queue` name | Ignored — one shared list regardless of which queue was named | Each stream is a distinct namespace; two differently-named queues never see each other's messages | Yes — storage keyed per queue name |
| `group` | Ignored for isolation — `ack` in one group's consume call could remove a message from a *different* group's pending set, since there was only one shared pending list | Consumer groups fan out independently; group A acking never affects group B's view | Yes — cursor and pending-set keyed per `(queue, group)`, covered by T1.c's fan-out test |
| `delivery_count` | Hardcoded to `1` forever — never reflected an actual redelivery | Tracked per delivered message | Partially — `InMemoryQueue` increments a persistent per-`(queue, group, message)` counter on every delivery. Building T1.c's redelivery test surfaced a genuine, narrower divergence between the two real drivers themselves: Redis's `replay_from` (`XGROUP SETID`) resets the group's delivery-position pointer without incrementing the *already-pending* entry's own counter, so a message re-read after a replay reports `delivery_count == 1` again on Redis, not a higher number — and this is arguably correct, not a defect, given `replay_from`'s stated purpose is FR-TRI-09 replay-*reproducibility* (a replayed message should look like the first run, not a retry). `tests/queue/test_conformance.py`'s redelivery test asserts only what is portably true (the message is redelivered with a well-formed, positive `delivery_count`), documents this driver-specific nuance in its own docstring, and does not force artificial agreement between the fake and Redis on this one point. Genuine crash-then-redeliver `delivery_count` incrementing (no `replay_from` involved) remains Redis-driver-specific, already covered by `libs/queue/tests/test_redis_streams.py`'s own `XPENDING`-based test — there is no portable way to force that exact scenario through `QueuePort` alone, because neither concrete driver actually implements the abstract port's stated "or, if none, this consumer's own previously-delivered-but-unacked" fallback (both simplify to "only genuinely new entries," a divergence from the abstract docstring each driver already documents at its own call site, not something this amendment discovered). |

No other parameter or behaviour was found silently diverging between the
fake and the real drivers as of this audit.
