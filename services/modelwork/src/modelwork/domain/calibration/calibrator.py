"""Learned calibrator over token confidence, layout certainty, normalization
confidence, validator outcomes, field class, script, doc type,
print/handwriting, legibility band. TODO: FR-CNF-01.

Must be a trained model with feature attribution — FR-REV-15 needs the
attribution later for the review UI's "dominant calibrator features" text.
"""


def calibrate(features: dict, stratum: str) -> dict:
    raise NotImplementedError("TODO: FR-CNF-01/02 not implemented")
