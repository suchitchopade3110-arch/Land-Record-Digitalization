"""Smoke test — the FastAPI app must construct and mount every router
without error. TODO: replace with real unit tests as domain/api modules
grow past their current NotImplementedError stubs."""
from backend.main import app


def test_app_constructs():
    assert app.title == "backend"


def test_expected_routes_are_mounted():
    # app.routes may hold lazily-resolved _IncludedRouter wrappers rather
    # than APIRoute objects directly, so resolve via the generated OpenAPI
    # schema (the same source FastAPI's own /docs and /openapi.json use).
    paths = set(app.openapi()["paths"].keys())
    for expected in ("/documents", "/config/{scope}/{key}", "/closed-sets/{type}", "/review-tasks", "/conflicts", "/dashboard/metrics", "/healthz"):
        assert expected in paths, f"{expected} not mounted — check gateway/route_registry.yaml stays in sync"
