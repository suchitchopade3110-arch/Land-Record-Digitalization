"""Constraint accounting — the schema-level invariant on ValidationResult.
TODO: FR-VAL-09 (P0, even though the levers that trigger it — FR-EXT-05,
FR-OCR-09 — are P1). Retrofitting this after records exist means migrating
them, so it ships now.

Consumes: which corpus/constraint Shree's decoder used, so this can mark the
corresponding validator not_applicable instead of pass.
"""


def force_not_applicable_if_constrained(verdict: str, consumed_constraints: list[str]) -> str:
    if consumed_constraints:
        return "not_applicable"
    return verdict
