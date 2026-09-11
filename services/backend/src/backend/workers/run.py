"""T1-02 / D3 — Worker process entrypoint.

Run via:
    python -m backend.workers.run <worker>
where <worker> is one of:
    - ingestion (consumes INGESTION_QUEUE)
    - triage (consumes TRIAGE_QUEUE)
    - decision (consumes DECISION_QUEUE)
    - outbox-relay (or relay: runs outbox relay drain loop)
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys

from landqueue import get_queue

from backend.models.base import session_factory
from backend.workers.decision_engine import handle as decision_handle
from backend.workers.ingestion_consumer import handle as ingestion_handle
from backend.workers.runner import RelayRunner, WorkerRunner
from backend.workers.triage_router import handle as triage_handle

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backend.workers.run")

WORKER_CONFIGS = {
    "ingestion": {
        "queue_name": "INGESTION_QUEUE",
        "handler": ingestion_handle,
    },
    "triage": {
        "queue_name": "TRIAGE_QUEUE",
        "handler": triage_handle,
    },
    "decision": {
        "queue_name": "DECISION_QUEUE",
        "handler": decision_handle,
    },
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run a backend worker or relay loop.")
    parser.add_argument(
        "worker",
        choices=["ingestion", "triage", "decision", "outbox-relay", "outbox_relay", "relay"],
        help="The worker type to run",
    )
    args = parser.parse_args(argv)

    worker_type = args.worker.replace("_", "-")
    logger.info("Starting worker: %s", worker_type)

    queue = get_queue()
    sf = session_factory()

    if worker_type in ("outbox-relay", "relay"):
        relay_runner = RelayRunner(session_factory=sf, queue=queue)
        relay_runner.install_signal_handlers()
        logger.info("Outbox relay runner started")
        relay_runner.run_loop()
        logger.info("Outbox relay runner exited")
        return

    cfg = WORKER_CONFIGS[worker_type]
    group_name = f"backend.{worker_type}"
    consumer_name = f"{socket.gethostname()}-{os.getpid()}"

    runner = WorkerRunner(
        queue=queue,
        queue_name=cfg["queue_name"],
        group=group_name,
        consumer_name=consumer_name,
        handler=cfg["handler"],
        session_factory=sf,
    )
    runner.install_signal_handlers()
    logger.info(
        "WorkerRunner started for %s (queue=%s, group=%s, consumer=%s)",
        worker_type,
        cfg["queue_name"],
        group_name,
        consumer_name,
    )
    runner.run_loop()
    logger.info("WorkerRunner %s exited", worker_type)


if __name__ == "__main__":
    main(sys.argv[1:])
