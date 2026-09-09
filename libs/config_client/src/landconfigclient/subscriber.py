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
    block_ms: int = 0,
) -> int:
    """Consume up to `count` pending invalidation messages once and apply
    each to `client`. Returns the number processed. `block_ms=0` (the
    default) makes this non-blocking, suited to being called from a
    request-handling loop or a test; a long-running subscriber process
    just calls this repeatedly with `block_ms>0`, the same shape every
    other worker in this repo already uses (ADR-003).
    """
    messages = queue.consume(QUEUE_NAME, group, consumer_name, count=count, block_ms=block_ms)
    for message in messages:
        client.handle_version_change_event(message.data)
        queue.ack(QUEUE_NAME, group, message.sequence_id)
    return len(messages)
