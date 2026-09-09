"""NATS JetStream driver — the pilot/prod broker target
(`docs/adr/ADR-003-queue-port.md`). Implemented against `QueuePort`'s
contract; **not exercised against a live NATS server in this environment**
(no NATS install here) — treat as reviewed-but-unverified until it is run
against a real JetStream instance. Requires the `nats` extra
(`pip install "landqueue[nats]"`).

`nats-py` is asyncio-native; `QueuePort` is a synchronous interface (every
P0 worker in this repo is a plain synchronous consume-loop, matching the
Redis Streams driver). This driver bridges the two with one dedicated event
loop per instance rather than a bare `asyncio.run()` per call, so a
`JetStreamQueue` used across many `consume()`/`publish()` calls in one
worker process doesn't pay event-loop start/teardown cost on every call and
doesn't lose a persistent NATS connection between them.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from landqueue.port import QueueMessage, QueuePort

try:
    import nats
    from nats.js.api import ConsumerConfig, DeliverPolicy
except ImportError as e:  # pragma: no cover — nats extra not installed
    raise ImportError(
        "landqueue.drivers.nats_jetstream requires the 'nats' extra: "
        "pip install 'landqueue[nats]'"
    ) from e

_PAYLOAD_FIELD = "envelope"


class _Loop:
    """One background event loop, one NATS connection, reused across calls."""

    def __init__(self, url: str):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._nc = self.run(nats.connect(url)).result()
        self._js = self._nc.jetstream()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @property
    def js(self):
        return self._js


class JetStreamQueue(QueuePort):
    def __init__(self, url: str = "nats://localhost:4222"):
        self._conn = _Loop(url)

    def publish(self, queue: str, message: dict[str, Any]) -> str:
        ack = self._conn.run(self._conn.js.publish(queue, json.dumps(message).encode())).result()
        return str(ack.seq)

    def ensure_group(self, queue: str, group: str, *, start: str = "$") -> None:
        deliver_policy = DeliverPolicy.NEW if start == "$" else DeliverPolicy.ALL
        self._conn.run(
            self._conn.js.add_consumer(
                stream=queue,
                config=ConsumerConfig(durable_name=group, deliver_policy=deliver_policy),
            )
        ).result()

    def consume(
        self,
        queue: str,
        group: str,
        consumer_name: str,
        *,
        count: int = 1,
        block_ms: int = 1000,
    ) -> list[QueueMessage]:
        self.ensure_group(queue, group)

        async def _pull() -> list[QueueMessage]:
            sub = await self._conn.js.pull_subscribe(subject=queue, durable=group)
            try:
                msgs = await sub.fetch(count, timeout=block_ms / 1000)
            except TimeoutError:
                return []
            out = []
            for m in msgs:
                out.append(
                    QueueMessage(
                        sequence_id=str(m.metadata.sequence.stream),
                        data=json.loads(m.data),
                        delivery_count=m.metadata.num_delivered,
                    )
                )
                # NAK is implicit — we don't ack here; caller acks explicitly
                # via QueuePort.ack, consistent with the Redis driver.
                out[-1]._raw_msg = m  # type: ignore[attr-defined]
            self._pending = {q.sequence_id: q._raw_msg for q in out}  # type: ignore[attr-defined]
            return out

        return self._conn.run(_pull()).result()

    def ack(self, queue: str, group: str, sequence_id: str) -> None:
        raw = getattr(self, "_pending", {}).get(sequence_id)
        if raw is None:
            raise KeyError(
                f"no pending NATS message for sequence_id={sequence_id!r} — "
                "ack() must be called in-process, after the matching consume()"
            )

        async def _ack():
            await raw.ack()

        self._conn.run(_ack()).result()

    def replay_from(self, queue: str, group: str, sequence_id: str) -> None:
        # Recreate the durable consumer with an explicit start sequence —
        # JetStream consumers are position-fixed at creation, unlike Redis
        # Streams' mutable XGROUP SETID.
        async def _replay():
            await self._conn.js.delete_consumer(queue, group)
            await self._conn.js.add_consumer(
                stream=queue,
                config=ConsumerConfig(
                    durable_name=group,
                    deliver_policy=DeliverPolicy.BY_START_SEQUENCE,
                    opt_start_seq=int(sequence_id) + 1,
                ),
            )

        self._conn.run(_replay()).result()
