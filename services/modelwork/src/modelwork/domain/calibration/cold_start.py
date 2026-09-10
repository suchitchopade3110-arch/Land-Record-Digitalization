"""Enforced cold-start ramp: Shadow -> Ramp -> Steady -> Regression (FR-CNF-15).

State machine governing automated acceptance and audit posture for every
new district, document type, or newly promoted model.
A newly promoted model enters in the SHADOW state and never inherits
the incumbent model's operating state or thresholds (FR-CNF-15, FR-LRN-08).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ColdStartState(str, Enum):
    """The four operational states of the cold-start ramp (FR-CNF-15)."""

    SHADOW = "shadow"
    RAMP = "ramp"
    STEADY = "steady"
    REGRESSION = "regression"


class InvalidStateTransition(ValueError):
    """Raised when an illegal cold-start state transition is attempted."""


VALID_TRANSITIONS: dict[ColdStartState, frozenset[ColdStartState]] = {
    ColdStartState.SHADOW: frozenset({
        ColdStartState.SHADOW,
        ColdStartState.RAMP,
    }),
    ColdStartState.RAMP: frozenset({
        ColdStartState.RAMP,
        ColdStartState.STEADY,
        ColdStartState.REGRESSION,
    }),
    ColdStartState.STEADY: frozenset({
        ColdStartState.STEADY,
        ColdStartState.REGRESSION,
    }),
    ColdStartState.REGRESSION: frozenset({
        ColdStartState.REGRESSION,
        ColdStartState.RAMP,
    }),
}


def validate_transition(from_state: ColdStartState, to_state: ColdStartState) -> None:
    """Validate that transitioning from `from_state` to `to_state` is legal.

    Raises InvalidStateTransition if the transition is illegal (e.g. SHADOW -> STEADY,
    SHADOW -> REGRESSION, or REGRESSION -> STEADY).
    """
    allowed = VALID_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise InvalidStateTransition(
            f"Illegal state transition from {from_state.value} to {to_state.value}. "
            f"Allowed targets: {sorted(s.value for s in allowed)}"
        )


@dataclass(frozen=True)
class OperationalPosture:
    """Operating policy flags associated with each cold-start state (FR-CNF-15)."""

    auto_accept_allowed: bool
    conservative_error_target: bool
    elevated_audit_sample: bool


def get_operational_posture(state: ColdStartState) -> OperationalPosture:
    """Return the operational posture for a given cold-start state.

    - SHADOW: Auto-accept disabled; conservative error target; elevated audit sampling.
    - RAMP: Conservative auto-accept enabled; conservative error target; elevated audit sampling.
    - STEADY: Normal auto-accept enabled; standard error target; standard audit sampling.
    - REGRESSION: Auto-accept disabled; conservative error target; elevated audit sampling.
    """
    if state == ColdStartState.SHADOW:
        return OperationalPosture(
            auto_accept_allowed=False,
            conservative_error_target=True,
            elevated_audit_sample=True,
        )
    elif state == ColdStartState.RAMP:
        return OperationalPosture(
            auto_accept_allowed=True,
            conservative_error_target=True,
            elevated_audit_sample=True,
        )
    elif state == ColdStartState.STEADY:
        return OperationalPosture(
            auto_accept_allowed=True,
            conservative_error_target=False,
            elevated_audit_sample=False,
        )
    elif state == ColdStartState.REGRESSION:
        return OperationalPosture(
            auto_accept_allowed=False,
            conservative_error_target=True,
            elevated_audit_sample=True,
        )
    else:
        raise ValueError(f"Unrecognized state: {state!r}")


@dataclass(frozen=True)
class ColdStartThresholds:
    """Configurable evidence thresholds for state transitions (FR-CNF-15).

    All numeric thresholds are required configuration parameters from Config Service (FR-CFG-01/02),
    never hard-coded or defaulted to zero.
    """

    min_shadow_evidence: int
    min_ramp_evidence: int
    min_recovery_evidence: int


@dataclass(frozen=True)
class ColdStartEvidence:
    """Observed metrics and flags evaluated for state transition decisions."""

    sample_count: int = 0
    calibration_verified: bool = False
    error_target_met: bool = False
    regression_alert: bool = False
    recovery_evidence_verified: bool = False


def next_state(
    current: ColdStartState,
    exit_conditions_met: bool = False,
    regression_alert: bool = False,
    *,
    recovery_conditions_met: bool = False,
) -> ColdStartState:
    """Determine the next ColdStartState given evaluation predicates.

    - SHADOW: transitions to RAMP if exit_conditions_met; else remains SHADOW.
      (Regression handling applies only after the model enters an operating state).
    - RAMP: transitions to REGRESSION if regression_alert; transitions to STEADY if exit_conditions_met; else remains RAMP.
    - STEADY: transitions to REGRESSION if regression_alert; else remains STEADY.
    - REGRESSION: transitions to RAMP if recovery_conditions_met; else remains REGRESSION.

    Validates transition legality before returning.
    """
    if current == ColdStartState.SHADOW:
        # In SHADOW, the model is not live; regression alerts are not operational.
        # Insufficient evidence or unverified conditions keep the model in SHADOW.
        target = ColdStartState.RAMP if exit_conditions_met else ColdStartState.SHADOW
    elif current == ColdStartState.RAMP:
        if regression_alert:
            target = ColdStartState.REGRESSION
        elif exit_conditions_met:
            target = ColdStartState.STEADY
        else:
            target = ColdStartState.RAMP
    elif current == ColdStartState.STEADY:
        if regression_alert:
            target = ColdStartState.REGRESSION
        else:
            target = ColdStartState.STEADY
    elif current == ColdStartState.REGRESSION:
        if regression_alert or not recovery_conditions_met:
            target = ColdStartState.REGRESSION
        else:
            target = ColdStartState.RAMP
    else:
        raise InvalidStateTransition(f"Unrecognized state: {current!r}")

    validate_transition(current, target)
    return target


def evaluate_cold_start_transition(
    current: ColdStartState,
    evidence: ColdStartEvidence,
    thresholds: ColdStartThresholds,
) -> ColdStartState:
    """Evaluate observed evidence against configured thresholds to compute next state."""
    if current == ColdStartState.SHADOW:
        exit_met = evidence.sample_count >= thresholds.min_shadow_evidence
        return next_state(current, exit_conditions_met=exit_met)

    elif current == ColdStartState.RAMP:
        if evidence.regression_alert:
            return next_state(current, regression_alert=True)
        exit_met = (
            evidence.sample_count >= thresholds.min_ramp_evidence
            and evidence.calibration_verified
            and evidence.error_target_met
        )
        return next_state(current, exit_conditions_met=exit_met)

    elif current == ColdStartState.STEADY:
        if evidence.regression_alert:
            return next_state(current, regression_alert=True)
        return next_state(current)

    elif current == ColdStartState.REGRESSION:
        if evidence.regression_alert:
            return next_state(current, regression_alert=True)
        recovery_met = (
            evidence.recovery_evidence_verified
            and evidence.sample_count >= thresholds.min_recovery_evidence
        )
        return next_state(current, recovery_conditions_met=recovery_met)

    else:
        raise InvalidStateTransition(f"Unrecognized state: {current!r}")

