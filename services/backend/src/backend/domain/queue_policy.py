"""T1-02 / D4 — Queue and worker runner policies.

Every threshold/parameter lives here with its config key named in a comment next
to it until served from ConfigVersion (T1-06 / FR-CFG-01).
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Config key: queue.block_ms
# Number of milliseconds worker runner blocks waiting for messages on consume()
DEFAULT_BLOCK_MS = 1000

# Config key: queue.batch_count
# Number of messages worker runner requests per consume() batch
DEFAULT_BATCH_COUNT = 1

# Config key: queue.max_deliveries
# Maximum number of delivery attempts before dead-lettering to <QUEUE>.DLQ
DEFAULT_MAX_DELIVERIES = 5

# Config key: outbox.relay_interval_ms
# Interval in milliseconds between outbox drain cycles when idle
DEFAULT_RELAY_INTERVAL_MS = 500

# Config key: outbox.relay_batch_size
# Maximum number of outbox rows to drain and publish in one drain_once cycle
DEFAULT_RELAY_BATCH_SIZE = 100


@dataclass(frozen=True)
class QueuePolicy:
    block_ms: int = DEFAULT_BLOCK_MS
    batch_count: int = DEFAULT_BATCH_COUNT
    max_deliveries: int = DEFAULT_MAX_DELIVERIES
    relay_interval_ms: int = DEFAULT_RELAY_INTERVAL_MS
    relay_batch_size: int = DEFAULT_RELAY_BATCH_SIZE


def get_queue_policy() -> QueuePolicy:
    """Read queue runner policy from environment / defaults until ConfigVersion."""
    try:
        block_ms = int(os.environ.get("QUEUE_BLOCK_MS", str(DEFAULT_BLOCK_MS)))
    except ValueError:
        block_ms = DEFAULT_BLOCK_MS

    try:
        batch_count = int(os.environ.get("QUEUE_BATCH_COUNT", str(DEFAULT_BATCH_COUNT)))
    except ValueError:
        batch_count = DEFAULT_BATCH_COUNT

    try:
        max_deliveries = int(os.environ.get("QUEUE_MAX_DELIVERIES", str(DEFAULT_MAX_DELIVERIES)))
    except ValueError:
        max_deliveries = DEFAULT_MAX_DELIVERIES

    try:
        relay_interval_ms = int(os.environ.get("OUTBOX_RELAY_INTERVAL_MS", str(DEFAULT_RELAY_INTERVAL_MS)))
    except ValueError:
        relay_interval_ms = DEFAULT_RELAY_INTERVAL_MS

    try:
        relay_batch_size = int(os.environ.get("OUTBOX_RELAY_BATCH_SIZE", str(DEFAULT_RELAY_BATCH_SIZE)))
    except ValueError:
        relay_batch_size = DEFAULT_RELAY_BATCH_SIZE

    return QueuePolicy(
        block_ms=block_ms,
        batch_count=batch_count,
        max_deliveries=max_deliveries,
        relay_interval_ms=relay_interval_ms,
        relay_batch_size=relay_batch_size,
    )
