"""Consumes CONFIDENCE_NOVELTY_QUEUE (ValidationResult[] + dedup candidates
from Shruthi). Computes novelty FIRST (gates everything else), then
per-stratum calibrated confidence, then the three-way routing signal.
TODO: FR-CNF-01/02/03/04/14/15."""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    # TODO: FR-CNF-14 — novelty score computed first; high novelty routes to
    # outside_calibrated_regime and NEVER falls through to the pooled
    # calibrator fallback, regardless of confidence.
    raise NotImplementedError("TODO: FR-CNF-01..04/14/15 not implemented")
