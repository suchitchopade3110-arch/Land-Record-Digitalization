"""Unit tests for the Cold Start state machine (FR-CNF-15)."""
from __future__ import annotations

import pytest

from modelwork.domain.calibration.cold_start import (
    ColdStartEvidence,
    ColdStartState,
    ColdStartThresholds,
    InvalidStateTransition,
    evaluate_cold_start_transition,
    get_operational_posture,
    next_state,
    validate_transition,
)


def test_initial_state_and_operational_posture():
    """Initial state is SHADOW with conservative auto-accept disabled."""
    initial = ColdStartState.SHADOW
    posture = get_operational_posture(initial)

    assert posture.auto_accept_allowed is False
    assert posture.conservative_error_target is True
    assert posture.elevated_audit_sample is True


def test_shadow_remains_shadow_when_evidence_insufficient():
    """SHADOW remains SHADOW when minimum required evidence is not yet met."""
    # Predicate-based transition
    assert next_state(ColdStartState.SHADOW, exit_conditions_met=False) == ColdStartState.SHADOW

    # Structured evaluation with configurable thresholds
    thresholds = ColdStartThresholds(
        min_shadow_evidence=50,
        min_ramp_evidence=100,
        min_recovery_evidence=25,
    )
    evidence = ColdStartEvidence(sample_count=49)
    result = evaluate_cold_start_transition(ColdStartState.SHADOW, evidence, thresholds)

    assert result == ColdStartState.SHADOW


def test_shadow_does_not_transition_to_regression():
    """SHADOW is an offline observation state; regression alerts apply only to live operating states."""
    # A regression alert does not trigger REGRESSION from SHADOW; it remains SHADOW
    assert next_state(ColdStartState.SHADOW, regression_alert=True) == ColdStartState.SHADOW

    # Direct transition from SHADOW to REGRESSION is explicitly invalid
    with pytest.raises(InvalidStateTransition):
        validate_transition(ColdStartState.SHADOW, ColdStartState.REGRESSION)


def test_shadow_transitions_to_ramp_when_evidence_satisfied():
    """SHADOW transitions to RAMP once configured evidence threshold is reached."""
    # Predicate-based transition
    assert next_state(ColdStartState.SHADOW, exit_conditions_met=True) == ColdStartState.RAMP

    # Structured evaluation
    thresholds = ColdStartThresholds(
        min_shadow_evidence=50,
        min_ramp_evidence=100,
        min_recovery_evidence=25,
    )
    evidence = ColdStartEvidence(sample_count=50)
    result = evaluate_cold_start_transition(ColdStartState.SHADOW, evidence, thresholds)

    assert result == ColdStartState.RAMP

    # Operational posture in RAMP enables conservative auto-accept with elevated audit
    ramp_posture = get_operational_posture(result)
    assert ramp_posture.auto_accept_allowed is True
    assert ramp_posture.conservative_error_target is True
    assert ramp_posture.elevated_audit_sample is True


def test_ramp_remains_conservative_when_criteria_not_satisfied():
    """RAMP remains in RAMP if sample count or calibration criteria are not met."""
    thresholds = ColdStartThresholds(
        min_shadow_evidence=50,
        min_ramp_evidence=100,
        min_recovery_evidence=25,
    )

    # Sample count insufficient
    evidence_count_low = ColdStartEvidence(
        sample_count=99,
        calibration_verified=True,
        error_target_met=True,
    )
    assert evaluate_cold_start_transition(ColdStartState.RAMP, evidence_count_low, thresholds) == ColdStartState.RAMP

    # Calibration not verified
    evidence_calib_false = ColdStartEvidence(
        sample_count=100,
        calibration_verified=False,
        error_target_met=True,
    )
    assert evaluate_cold_start_transition(ColdStartState.RAMP, evidence_calib_false, thresholds) == ColdStartState.RAMP

    # Error target not met
    evidence_target_false = ColdStartEvidence(
        sample_count=100,
        calibration_verified=True,
        error_target_met=False,
    )
    assert evaluate_cold_start_transition(ColdStartState.RAMP, evidence_target_false, thresholds) == ColdStartState.RAMP


def test_ramp_transitions_to_steady_only_when_all_criteria_satisfied():
    """RAMP transitions to STEADY only when samples, calibration, and error targets are satisfied."""
    # Predicate-based
    assert next_state(ColdStartState.RAMP, exit_conditions_met=True) == ColdStartState.STEADY

    # Structured evaluation
    thresholds = ColdStartThresholds(
        min_shadow_evidence=50,
        min_ramp_evidence=100,
        min_recovery_evidence=25,
    )
    evidence = ColdStartEvidence(
        sample_count=100,
        calibration_verified=True,
        error_target_met=True,
    )
    result = evaluate_cold_start_transition(ColdStartState.RAMP, evidence, thresholds)

    assert result == ColdStartState.STEADY

    # Operational posture in STEADY uses standard error target and standard audit sampling
    steady_posture = get_operational_posture(result)
    assert steady_posture.auto_accept_allowed is True
    assert steady_posture.conservative_error_target is False
    assert steady_posture.elevated_audit_sample is False


def test_steady_remains_steady_and_does_not_silently_transition():
    """STEADY does not transition merely because more samples accumulate."""
    thresholds = ColdStartThresholds(
        min_shadow_evidence=50,
        min_ramp_evidence=100,
        min_recovery_evidence=25,
    )
    evidence = ColdStartEvidence(
        sample_count=10000,
        calibration_verified=True,
        error_target_met=True,
    )
    result = evaluate_cold_start_transition(ColdStartState.STEADY, evidence, thresholds)

    assert result == ColdStartState.STEADY
    assert next_state(ColdStartState.STEADY, exit_conditions_met=True) == ColdStartState.STEADY


def test_regression_condition_triggers_transition_to_regression():
    """Regression alerts move system into REGRESSION state from live operating states (STEADY, RAMP)."""
    assert next_state(ColdStartState.STEADY, regression_alert=True) == ColdStartState.REGRESSION
    assert next_state(ColdStartState.RAMP, regression_alert=True) == ColdStartState.REGRESSION

    # Posture in REGRESSION disables auto-accept
    regression_posture = get_operational_posture(ColdStartState.REGRESSION)
    assert regression_posture.auto_accept_allowed is False
    assert regression_posture.conservative_error_target is True
    assert regression_posture.elevated_audit_sample is True


def test_regression_does_not_jump_to_steady_without_recovery_criteria():
    """REGRESSION never jumps directly to STEADY; requires explicit recovery."""
    # Without recovery conditions met, remains REGRESSION
    assert next_state(ColdStartState.REGRESSION, exit_conditions_met=True, recovery_conditions_met=False) == ColdStartState.REGRESSION

    # Direct transition from REGRESSION to STEADY is illegal
    with pytest.raises(InvalidStateTransition):
        validate_transition(ColdStartState.REGRESSION, ColdStartState.STEADY)


def test_regression_recovers_to_ramp_when_recovery_criteria_satisfied():
    """REGRESSION recovers to RAMP when explicit recovery criteria and sample counts are verified."""
    # Predicate-based
    assert next_state(ColdStartState.REGRESSION, recovery_conditions_met=True) == ColdStartState.RAMP

    # Structured evaluation
    thresholds = ColdStartThresholds(
        min_shadow_evidence=50,
        min_ramp_evidence=100,
        min_recovery_evidence=25,
    )

    # Insufficient recovery sample count
    evidence_insufficient = ColdStartEvidence(
        sample_count=24,
        recovery_evidence_verified=True,
    )
    assert evaluate_cold_start_transition(ColdStartState.REGRESSION, evidence_insufficient, thresholds) == ColdStartState.REGRESSION

    # Sufficient recovery sample count and verified evidence
    evidence_recovered = ColdStartEvidence(
        sample_count=25,
        recovery_evidence_verified=True,
    )
    result = evaluate_cold_start_transition(ColdStartState.REGRESSION, evidence_recovered, thresholds)
    assert result == ColdStartState.RAMP


def test_invalid_transitions_rejected_explicitly():
    """Directly jumping across non-adjacent pipeline states is rejected."""
    with pytest.raises(InvalidStateTransition):
        validate_transition(ColdStartState.SHADOW, ColdStartState.STEADY)

    with pytest.raises(InvalidStateTransition):
        validate_transition(ColdStartState.SHADOW, ColdStartState.REGRESSION)

    with pytest.raises(InvalidStateTransition):
        validate_transition(ColdStartState.STEADY, ColdStartState.RAMP)

    with pytest.raises(InvalidStateTransition):
        validate_transition(ColdStartState.STEADY, ColdStartState.SHADOW)

    with pytest.raises(InvalidStateTransition):
        validate_transition(ColdStartState.RAMP, ColdStartState.SHADOW)


def test_deterministic_behavior_for_identical_inputs():
    """State machine decisions are completely deterministic and side-effect free."""
    thresholds = ColdStartThresholds(
        min_shadow_evidence=30,
        min_ramp_evidence=60,
        min_recovery_evidence=15,
    )
    evidence = ColdStartEvidence(sample_count=35, calibration_verified=False)

    res1 = evaluate_cold_start_transition(ColdStartState.SHADOW, evidence, thresholds)
    res2 = evaluate_cold_start_transition(ColdStartState.SHADOW, evidence, thresholds)
    res3 = evaluate_cold_start_transition(ColdStartState.SHADOW, evidence, thresholds)

    assert res1 == res2 == res3 == ColdStartState.RAMP
