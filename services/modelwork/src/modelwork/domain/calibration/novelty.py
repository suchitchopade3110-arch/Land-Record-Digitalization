"""Novelty detection — computed FIRST, from the recognition backbone's
embedding distribution. TODO: FR-CNF-14. Gates the calibrated regime itself;
the pooled-calibrator fallback quietly scoring novel input is the dangerous
behavior here, not the safe one."""


def score_novelty(embedding: list[float], stratum: str) -> float:
    raise NotImplementedError("TODO: FR-CNF-14 not implemented")
