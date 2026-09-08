"""Consumes TRIAGE_QUEUE (fully classified Page + pinned WorkEnvelope) and
routes internally to the text and/or map pipeline per Page.route.
TODO: FR-TRI-05.
"""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    raise NotImplementedError("TODO: FR-TRI-05 not implemented")
