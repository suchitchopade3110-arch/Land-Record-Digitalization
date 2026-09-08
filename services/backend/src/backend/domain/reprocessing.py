"""FR-CFG-04 reprocessing job (P1) + FR-CFG-05 impact preview (P1).
Given a corrected config version, re-derive affected records, diff against
published values, publish NEW versions (never mutate old ones), route only
material diffs to review.
"""


def impact_preview(config_version: str) -> dict:
    raise NotImplementedError("TODO: FR-CFG-05 (P1) not implemented")


def reprocess(config_version: str) -> None:
    raise NotImplementedError("TODO: FR-CFG-04 (P1) not implemented")
