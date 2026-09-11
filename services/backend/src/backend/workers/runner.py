"""T1-02 — Queue infrastructure: Worker runner and relay loop.

Implements D1 (at-least-once, ack-after-commit, DLQ on max_deliveries with audit entry),
D2 (runner owns the DB session and single commit; handlers only flush),
and D4 (queue/outbox policies via backend.domain.queue_policy).
"""
from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from typing import Any

from landoutbox.relay import Relay
from landqueue.port import QueuePort
from sqlalchemy.orm import Session, sessionmaker

from backend.domain.audit_log import record_dead_lettered
from backend.domain.queue_policy import QueuePolicy, get_queue_policy

logger = logging.getLogger(__name__)


class WorkerRunner:
    """Generic worker runner for QueuePort consumer loops.

    Owns DB session lifecycle, single transaction commit, ack-after-commit,
    and dead-letter routing to <QUEUE>.DLQ upon exceeding max_deliveries.
    """

    def __init__(
        self,
        queue: QueuePort,
        queue_name: str,
        group: str,
        consumer_name: str,
        handler: Callable[[dict[str, Any], Session], Any],
        session_factory: sessionmaker[Session],
        *,
        max_deliveries: int | None = None,
        block_ms: int | None = None,
        batch_count: int | None = None,
        policy: QueuePolicy | None = None,
    ) -> None:
        pol = policy or get_queue_policy()
        self.queue = queue
        self.queue_name = queue_name
        self.group = group
        self.consumer_name = consumer_name
        self.handler = handler
        self.session_factory = session_factory
        self.max_deliveries = max_deliveries if max_deliveries is not None else pol.max_deliveries
        self.block_ms = block_ms if block_ms is not None else pol.block_ms
        self.batch_count = batch_count if batch_count is not None else pol.batch_count
        self._should_stop = False

    @property
    def should_stop(self) -> bool:
        return self._should_stop

    def stop(self) -> None:
        self._should_stop = True

    def install_signal_handlers(self) -> None:
        """Register graceful shutdown hooks for SIGINT and SIGTERM."""
        def _handle_sig(signum: int, _frame: Any) -> None:
            logger.info("Worker %s received signal %s, initiating graceful stop", self.consumer_name, signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, _handle_sig)
            signal.signal(signal.SIGTERM, _handle_sig)
        except (ValueError, AttributeError):
            # Signal handling might not be available in non-main threads
            pass

    def run_once(self) -> int:
        """Consume and process up to batch_count messages. Returns count processed."""
        if self._should_stop:
            return 0

        messages = self.queue.consume(
            self.queue_name,
            self.group,
            self.consumer_name,
            count=self.batch_count,
            block_ms=self.block_ms,
        )
        if not messages:
            return 0

        processed = 0
        for msg in messages:
            if self._should_stop:
                # Stop requested; do not start processing new message in batch
                break

            # If already exceeded max_deliveries, dead-letter immediately
            if msg.delivery_count > self.max_deliveries:
                self._dead_letter(msg, error_class="MaxDeliveriesExceeded")
                processed += 1
                continue

            with self.session_factory() as session:
                try:
                    self.handler(msg.data, session)
                    session.commit()
                    self.queue.ack(self.queue_name, self.group, msg.sequence_id)
                    processed += 1
                except Exception as exc:
                    session.rollback()
                    logger.exception("Handler failed for message %s (delivery_count=%s)", msg.sequence_id, msg.delivery_count)
                    if msg.delivery_count >= self.max_deliveries:
                        # Poison message has reached max allowed delivery attempts
                        self._dead_letter(msg, error_class=type(exc).__name__)
                        processed += 1
                    else:
                        # Re-raise so caller/loop knows and message stays unacked for redelivery
                        raise

        return processed

    def _dead_letter(self, msg: Any, *, error_class: str) -> None:
        """Move message to <QUEUE>.DLQ, log audit entry without payload values, and ack main stream."""
        dlq_name = f"{self.queue_name}.DLQ"
        self.queue.publish(dlq_name, msg.data)

        with self.session_factory() as audit_session:
            record_dead_lettered(
                audit_session,
                queue=self.queue_name,
                message_id=msg.sequence_id,
                delivery_count=msg.delivery_count,
                error_class=error_class,
            )
            audit_session.commit()

        self.queue.ack(self.queue_name, self.group, msg.sequence_id)
        logger.warning(
            "Message %s moved to DLQ %s after %s deliveries (error: %s)",
            msg.sequence_id,
            dlq_name,
            msg.delivery_count,
            error_class,
        )

    def run_loop(self, *, max_iterations: int | None = None) -> None:
        """Continuously consume messages until stopped or max_iterations reached."""
        iterations = 0
        while not self._should_stop:
            if max_iterations is not None and iterations >= max_iterations:
                break
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 — the loop must survive any handler/driver failure; run_once already logged and rolled back
                logger.warning("Error during worker execution cycle, continuing loop")
            iterations += 1


def run_worker_once(
    queue: QueuePort,
    queue_name: str,
    group: str,
    consumer_name: str,
    handler: Callable[[dict[str, Any], Session], Any],
    session_factory: sessionmaker[Session],
    *,
    max_deliveries: int | None = None,
    block_ms: int | None = None,
    batch_count: int | None = None,
) -> int:
    """Convenience helper to run a single worker iteration."""
    runner = WorkerRunner(
        queue=queue,
        queue_name=queue_name,
        group=group,
        consumer_name=consumer_name,
        handler=handler,
        session_factory=session_factory,
        max_deliveries=max_deliveries,
        block_ms=block_ms,
        batch_count=batch_count,
    )
    return runner.run_once()


class RelayRunner:
    """Outbox relay loop runner."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        queue: QueuePort,
        *,
        interval_ms: int | None = None,
        batch_size: int | None = None,
        policy: QueuePolicy | None = None,
    ) -> None:
        pol = policy or get_queue_policy()
        self.session_factory = session_factory
        self.queue = queue
        self.interval_ms = interval_ms if interval_ms is not None else pol.relay_interval_ms
        self.batch_size = batch_size if batch_size is not None else pol.relay_batch_size
        self.relay = Relay(session_factory=session_factory, queue=queue)
        self._should_stop = False

    @property
    def should_stop(self) -> bool:
        return self._should_stop

    def stop(self) -> None:
        self._should_stop = True

    def install_signal_handlers(self) -> None:
        def _handle_sig(signum: int, _frame: Any) -> None:
            logger.info("Relay received signal %s, stopping", signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, _handle_sig)
            signal.signal(signal.SIGTERM, _handle_sig)
        except (ValueError, AttributeError):
            pass

    def run_once(self) -> int:
        return self.relay.drain_once(batch_size=self.batch_size)

    def run_loop(self, *, max_iterations: int | None = None) -> None:
        iterations = 0
        while not self._should_stop:
            if max_iterations is not None and iterations >= max_iterations:
                break
            try:
                dispatched = self.run_once()
                if dispatched == 0 and not self._should_stop:
                    time.sleep(self.interval_ms / 1000.0)
            except Exception:
                logger.exception("Error during outbox relay drain cycle")
                time.sleep(self.interval_ms / 1000.0)
            iterations += 1
