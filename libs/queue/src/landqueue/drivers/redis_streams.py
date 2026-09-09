"""Redis Streams driver — default in local dev and CI
(`infra/docker-compose.yml`'s `redis` service). XADD/XREADGROUP/XACK
implementation of `landqueue.port.QueuePort`.
"""
from __future__ import annotations

import json
from typing import Any

import redis

from landqueue.port import QueueMessage, QueuePort

_PAYLOAD_FIELD = "envelope"


class RedisStreamsQueue(QueuePort):
    def __init__(self, client: redis.Redis | None = None, *, url: str | None = None):
        self._r = client or redis.Redis.from_url(url or "redis://localhost:6379/0", decode_responses=True)

    def publish(self, queue: str, message: dict[str, Any]) -> str:
        return self._r.xadd(queue, {_PAYLOAD_FIELD: json.dumps(message)})

    def ensure_group(self, queue: str, group: str, *, start: str = "$") -> None:
        # MKSTREAM: create the stream itself if this is the first group on it.
        try:
            self._r.xgroup_create(name=queue, groupname=group, id=start, mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            # Group already exists — idempotent no-op, matching the port's contract.

    def consume(
        self,
        queue: str,
        group: str,
        consumer_name: str,
        *,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[QueueMessage]:
        # A brand-new group starts from the beginning of the stream, not
        # "$" (now) — a group that didn't exist yet has processed nothing,
        # and starting it at "now" would silently drop every message
        # published before this worker came online, which is exactly the
        # loss window ADR-005's transactional outbox exists to close. An
        # already-existing group is left at its current position
        # (`ensure_group` is a no-op on BUSYGROUP).
        self.ensure_group(queue, group, start="0")
        # ">" = only genuinely new (never-delivered-to-this-group) entries.
        # A worker that wants its own unacked backlog first should pass "0"
        # via a second call — kept simple here since every P0 worker in
        # this repo processes-then-acks in the same loop iteration.
        resp = self._r.xreadgroup(
            groupname=group,
            consumername=consumer_name,
            streams={queue: ">"},
            count=count,
            block=block_ms,
        )
        if not resp:
            return []
        messages: list[QueueMessage] = []
        for _stream_name, entries in resp:
            for entry_id, fields in entries:
                data = json.loads(fields[_PAYLOAD_FIELD])
                pending = self._r.xpending_range(queue, group, min=entry_id, max=entry_id, count=1)
                delivery_count = pending[0]["times_delivered"] if pending else 1
                messages.append(QueueMessage(sequence_id=entry_id, data=data, delivery_count=delivery_count))
        return messages

    def ack(self, queue: str, group: str, sequence_id: str) -> None:
        self._r.xack(queue, group, sequence_id)

    def replay_from(self, queue: str, group: str, sequence_id: str) -> None:
        self.ensure_group(queue, group, start="0")
        self._r.xgroup_setid(name=queue, groupname=group, id=sequence_id)
