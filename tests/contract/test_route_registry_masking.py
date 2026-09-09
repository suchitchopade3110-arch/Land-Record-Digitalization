"""T4.c (the enumeration half) — drives directly off
`gateway/route_registry.yaml` so a new route with no declared masking
treatment fails by default, per the build prompt's own wording. No DB
needed: this only reads the registry file.
"""
from pathlib import Path

import pytest
import yaml

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "gateway" / "route_registry.yaml"

VALID_MASKING_VALUES = {
    "none",
    "masked_record_view",
    "unmasked_privileged_operation",
    "officer_working_view_out_of_scope",
}


def _routes() -> list[dict]:
    with open(REGISTRY_PATH) as f:
        doc = yaml.safe_load(f)
    return doc["routes"]


@pytest.mark.parametrize("route", _routes(), ids=lambda r: f"{r['method']} {r['path']}")
def test_every_route_declares_a_masking_treatment(route):
    """The build prompt's exact requirement: "a new route with no
    declared masking treatment fails by default." A route missing
    `masking` entirely, or spelling it wrong, fails this test — it must
    never be interpreted as "presumably fine."""
    assert "masking" in route, f"{route['method']} {route['path']} has no declared `masking` treatment"
    assert route["masking"] in VALID_MASKING_VALUES, (
        f"{route['method']} {route['path']} declares an unrecognised masking value {route['masking']!r} "
        f"— must be one of {sorted(VALID_MASKING_VALUES)}"
    )


def test_officer_working_view_exemption_is_only_used_on_review_task_routes():
    """The one masking value that means "deliberately not masked" is
    scoped narrowly — a future route accidentally reusing it to dodge
    masking elsewhere would be a real leak, not a documented exemption."""
    exempted = [r for r in _routes() if r.get("masking") == "officer_working_view_out_of_scope"]
    assert exempted, "expected at least the review-task crop/submit routes to carry this exemption"
    for route in exempted:
        assert route["path"].startswith("/review-tasks/"), (
            f"unexpected route claiming the officer-working-view exemption: {route['path']} "
            "— this exemption is scoped to the Phase 3 review workbench only, see PHASE4.md"
        )


def test_every_record_reading_route_is_masked_record_view_or_unmasked_privileged():
    """A structural check that a route whose path clearly returns record
    field data (`/records/...`) never gets `masking: none` — this
    doesn't replace the runtime T4.c test (which needs a live app+DB), but
    it catches the registry itself declaring something implausible."""
    for route in _routes():
        if route["path"].startswith("/records/") and route["method"] == "GET":
            assert route["masking"] == "masked_record_view"
