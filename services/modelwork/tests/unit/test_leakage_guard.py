"""FR-LRN-11 — the one P0 guard that protects every accuracy claim below it."""
import pytest

from modelwork.domain.learning_loop.guard import LeakageDetected, assert_no_leakage


def test_no_intersection_passes():
    assert_no_leakage({"a", "b"}, {"c", "d"})


def test_intersection_raises():
    with pytest.raises(LeakageDetected):
        assert_no_leakage({"a", "b"}, {"b", "c"})
