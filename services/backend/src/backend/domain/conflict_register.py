"""Conflict register (M10). TODO: FR-CFL-01-03.
Consumes verdicts from Shruthi's validators, doesn't produce them.
Publication is BLOCKED for any record with an open conflict.
"""


def open_conflict(records: list[str], rule: str, evidence: dict, origin: str) -> dict:
    raise NotImplementedError("TODO: FR-CFL-01 not implemented")


def is_publish_blocked(record_id: str) -> bool:
    raise NotImplementedError("TODO: FR-CFL-03 not implemented")
