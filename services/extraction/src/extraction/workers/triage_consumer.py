"""Consumes TRIAGE_QUEUE and routes internally to text/map pipelines (FR-TRI-05)."""

from __future__ import annotations

from observability import traced_consumer

from extraction.workers import text_lane


@traced_consumer
def handle(message: dict) -> dict:
    """Routes inbound page messages to text lane and/or map lane."""
    payload = message.get("payload", {})
    route = payload.get("route") or []
    page_role = payload.get("page_role")

    results = {}

    # Check if text lane processing is required
    if "text" in route or page_role in ("text", "tabular_register", "endorsement") or not route:
        results["text_lane"] = text_lane.handle(message)

    return results
