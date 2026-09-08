"""Consumes INGESTION_QUEUE. TODO: FR-ING-01-08.
contracts/asyncapi/triage-queue.yaml documents the outbound hop.
"""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    # TODO: FR-ING-07 scheduled fixity re-verification,
    # FR-ING-08 volume completeness (needs Shree's index_position).
    raise NotImplementedError("TODO: FR-ING-01..08 not implemented")
