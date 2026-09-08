"""Enforced cold-start ramp: Shadow -> Ramp -> Steady -> Regression, with
stated exit conditions. TODO: FR-CNF-15. Every new district/doc-type/model
is a cold start — not optional tuning, a state machine. A promoted model
does NOT inherit the incumbent's threshold."""
from enum import Enum


class ColdStartState(str, Enum):
    SHADOW = "shadow"
    RAMP = "ramp"
    STEADY = "steady"
    REGRESSION = "regression"


def next_state(current: ColdStartState, exit_conditions_met: bool, regression_alert: bool) -> ColdStartState:
    raise NotImplementedError("TODO: FR-CNF-15 not implemented")
