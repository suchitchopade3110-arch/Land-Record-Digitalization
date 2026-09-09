"""ADR-003 — `get_queue()` is the one call every service's publishers and
workers make to obtain a `QueuePort`. Driver selection is by
`QUEUE_DRIVER` env var, read once; never instantiate a driver class
directly outside this module and `libs/queue/tests/`.
"""
from __future__ import annotations

import os
from functools import lru_cache

from landqueue.port import QueueMessage, QueuePort

__all__ = ["QueueMessage", "QueuePort", "get_queue"]


@lru_cache(maxsize=1)
def get_queue() -> QueuePort:
    driver = os.environ.get("QUEUE_DRIVER", "redis_streams")
    if driver == "redis_streams":
        from landqueue.drivers.redis_streams import RedisStreamsQueue

        return RedisStreamsQueue(url=os.environ.get("QUEUE_URL", "redis://localhost:6379/0"))
    if driver == "nats_jetstream":
        from landqueue.drivers.nats_jetstream import JetStreamQueue

        return JetStreamQueue(url=os.environ.get("QUEUE_URL", "nats://localhost:4222"))
    raise ValueError(f"unknown QUEUE_DRIVER={driver!r} — expected 'redis_streams' or 'nats_jetstream'")
