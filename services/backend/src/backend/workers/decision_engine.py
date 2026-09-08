"""Consumes DECISION_QUEUE. Routes each Extraction to exactly one of
auto-accept / audit-sample / review / conflict / outside-regime, per
Architecture §15 (5 outcomes, not 3).

TODO: FR-CNF-04, FR-CFL-01. Reads `routing_outcome` set by Tharun — this
worker does not compute confidence/novelty itself, only acts on it.
"""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    raise NotImplementedError("TODO: FR-CNF-04/FR-CFL-01 not implemented")
