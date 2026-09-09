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
