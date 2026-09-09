"""Invariant 1 (CLAUDE.md) / FR-VAL-09 — consumed_constraints non-empty
forces verdict = not_applicable. A Postgres CHECK constraint
(`backend.models.entities.ValidationResult`), so a violating write raises
at the database, not a Python-level check someone could route around by
writing raw SQL or a different ORM."""
import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.entities import ValidationResult


def test_pass_verdict_with_no_consumed_constraints_is_allowed(session, a_record):
    session.add(
        ValidationResult(
            record_id=a_record.id, validator="syntactic", verdict="pass", consumed_constraints=[],
        )
    )
    session.flush()  # must not raise


def test_not_applicable_verdict_with_consumed_constraints_is_allowed(session, a_record):
    session.add(
        ValidationResult(
            record_id=a_record.id,
            validator="arithmetic",
            verdict="not_applicable",
            consumed_constraints=["gazetteer:land_classification_codes"],
        )
    )
    session.flush()  # must not raise


def test_pass_verdict_with_consumed_constraints_is_REJECTED_by_the_database(session, a_record):
    """The violation this invariant exists to prevent: a joint-decoding
    lever (FR-EXT-05) consumed a constraint and the validator tried to
    report `pass` anyway — the exact bug FR-VAL-09 exists to make
    unrepresentable."""
    session.add(
        ValidationResult(
            record_id=a_record.id,
            validator="arithmetic",
            verdict="pass",
            consumed_constraints=["gazetteer:land_classification_codes"],
        )
    )
    with pytest.raises(IntegrityError, match="ck_validation_result_consumed_constraints_forces_not_applicable"):
        session.flush()


def test_fail_verdict_with_consumed_constraints_is_also_REJECTED(session, a_record):
    """The invariant names exactly one legal verdict once a constraint is
    consumed — not_applicable — not "anything but pass"."""
    session.add(
        ValidationResult(
            record_id=a_record.id,
            validator="referential",
            verdict="fail",
            consumed_constraints=["lgd_codes"],
        )
    )
    with pytest.raises(IntegrityError, match="ck_validation_result_consumed_constraints_forces_not_applicable"):
        session.flush()
