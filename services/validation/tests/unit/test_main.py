"""Smoke test — the FastAPI app must construct. TODO: replace with real
unit tests as domain/workers modules grow past their current
NotImplementedError stubs."""
from validation.main import app


def test_app_constructs():
    assert app.title == "validation"
