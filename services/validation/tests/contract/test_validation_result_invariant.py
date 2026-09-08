"""FR-VAL-09 invariant, exercised locally against the pure function before
the DB-level trigger test in tests/contract/test_validation_result_invariant.py
(repo root) runs against a real database."""
from validation.domain.constraint_accounting import force_not_applicable_if_constrained


def test_consumed_constraints_forces_not_applicable():
    assert force_not_applicable_if_constrained("pass", ["gazetteer:sitapur"]) == "not_applicable"


def test_no_consumed_constraints_leaves_verdict_untouched():
    assert force_not_applicable_if_constrained("pass", []) == "pass"
