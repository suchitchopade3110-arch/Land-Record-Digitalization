"""Promotion gate: frozen regression suite (comparability) + per-stratum
ECE (blocking) + stated minimum detectable effect. Model + calibrator
promoted together, versioned together. TODO: FR-LRN-02/08, PRD §09 (MDE).
A promoted model enters the cold-start ramp — does NOT inherit the
incumbent's threshold."""


def evaluate_candidate(candidate_model_version: str, regression_suite_results: dict, ece_per_stratum: dict) -> dict:
    raise NotImplementedError("TODO: FR-LRN-02/08 not implemented")
