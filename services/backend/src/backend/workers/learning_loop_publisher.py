"""Publishes Correction rows to LEARNING_LOOP_QUEUE on officer submit from
the review workbench. TODO: FR-LRN-01/07.
"""
from observability import traced_consumer

from backend.publishers.learning_loop_publisher import (
    QUEUE_NAME,
    build_learning_loop_envelope,
    publish_correction_to_outbox,
)


@traced_consumer
def handle(message: dict) -> None:
    # Consumer handler placeholder for future learning loop consumption
    raise NotImplementedError("Learning loop queue consumer is owned by downstream learning worker")

