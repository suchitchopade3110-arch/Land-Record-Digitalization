"""Wires `ConfigClient.handle_version_change_event` to the shared broker
(ADR-003). Each service runs one instance of this as a background
consumer loop — this is the actual "never poll on a timer alone"
mechanism §4.1 requires; the client's own `ttl_seconds` is a backstop
only, and must keep working correctly with this subscriber never invoked
at all (that's what proves the event path isn't just the timer wearing a
different hat).
"""
from __future__ import annotations

from landqueue.port import QueuePort

from landconfigclient.client import ConfigClient

QUEUE_NAME = "config-version-events"


def drain_once(
    client: ConfigClient,
    queue: QueuePort,
    group: str,
    consumer_name: str,
    *,
    count: int = 10,
    block_ms: int = 50,
) -> int:
    """Consume up to `count` pending invalidation messages once and apply
    each to `client`. Returns the number processed. A long-running
    subscriber process just calls this repeatedly in a loop, the same
    shape every other worker in this repo already uses (ADR-003).

    P5-02b correction: this used to default `block_ms=0` and claim that
    made the call non-blocking. Against `landqueue`'s Redis Streams
    driver, `block_ms=0` is passed straight through to `XREADGROUP ...
    BLOCK 0`, which is Redis's own syntax for "block indefinitely" — the
    opposite of what the old docstring claimed. This went uncaught by
    P5-04's own unit tests because they run against an in-memory fake
    `QueuePort` that ignores `block_ms` entirely and always returns
    immediately; it surfaced only once P5-02b's write-workflow test
    exercised this function against a real Redis broker. `block_ms=50` is
    a short, real poll interval instead — short enough that a caller
    wanting non-blocking behaviour barely pays for it, and no caller can
    accidentally hang a request thread forever the way the old default
    would have in production.
    """
    messages = queue.consume(QUEUE_NAME, group, consumer_name, count=count, block_ms=block_ms)
    for message in messages:
        client.handle_version_change_event(message.data)
        queue.ack(QUEUE_NAME, group, message.sequence_id)
    return len(messages)
