"""Consumes TRIAGE_QUEUE after Tharun's classifiers have run. Pins the work
envelope (FR-TRI-09) and routes to TEXT_QUEUE / MAP_QUEUE / both.

This is the idempotence guarantee everything downstream depends on: a
retried message after a model promotion must reproduce its original result,
never a new one (API-Contracts-and-Interfaces.md §2, §7 rule 1).
"""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    # TODO: FR-TRI-09 — call GET /models/{module}/active (Tharun) once per
    # page to resolve model_versions, write the WorkEnvelope, then publish
    # to TEXT_QUEUE and/or MAP_QUEUE per Page.route. Never re-resolve
    # "current model" for a retried message — read the pinned envelope.
    raise NotImplementedError("TODO: FR-TRI-09 not implemented")
