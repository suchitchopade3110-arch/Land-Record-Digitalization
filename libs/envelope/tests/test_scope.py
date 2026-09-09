"""Pure-Python — no DB needed. Proves 'never resolve current model/config
mid-pipeline' (API-Contracts §7 rule 1) is enforced, not just documented."""
import pytest

from landenvelope.scope import (
    LiveResolutionInsidePinnedScope,
    current_pinned_envelope_id,
    guard_resolve_active,
    pinned_scope,
)


def test_guard_is_a_noop_outside_any_pinned_scope():
    assert current_pinned_envelope_id() is None
    guard_resolve_active("config_version")  # must not raise


def test_guard_raises_inside_a_pinned_scope():
    with pinned_scope("envelope-123"):
        with pytest.raises(LiveResolutionInsidePinnedScope) as exc_info:
            guard_resolve_active("model_versions.hwr")
        assert "envelope-123" in str(exc_info.value)
        assert "model_versions.hwr" in str(exc_info.value)


def test_scope_is_cleared_on_exit_even_after_an_exception():
    with pytest.raises(ValueError):
        with pinned_scope("envelope-abc"):
            raise ValueError("simulated handler failure")

    assert current_pinned_envelope_id() is None
    guard_resolve_active("config_version")  # scope cleared — no longer raises
