"""Consumes TRIAGE_QUEUE and routes internally to text/map pipelines (FR-TRI-05)."""

from __future__ import annotations

import logging

from observability import traced_consumer

from extraction.workers import map_lane, text_lane

logger = logging.getLogger(__name__)


@traced_consumer
def handle(message: dict) -> dict:
    """Routes inbound page messages to text lane and/or map lane."""
    if not isinstance(message, dict):
        message = {}

    payload = message.get("payload", {}) if isinstance(message.get("payload"), dict) else {}
    route = payload.get("route") or []
    page_role = payload.get("page_role")
    doc_type = payload.get("doc_type")

    results = {}

    # Determine routes
    should_route_text = (
        "text" in route
        or page_role in ("text", "tabular_register", "endorsement")
        or (not route and page_role != "map_sheet" and doc_type not in ("cadastral_map", "fmb_sketch"))
    )

    should_route_map = (
        "map" in route
        or page_role == "map_sheet"
        or doc_type in ("cadastral_map", "fmb_sketch")
        or "non_text_map" in route
        or "map_lane" in route
    )

    # 1. Text Lane Execution
    if should_route_text:
        try:
            results["text_lane"] = text_lane.handle(message)
        except Exception as err:
            logger.exception("Text lane error on message")
            results["text_lane"] = {
                "status": "error",
                "error_type": type(err).__name__,
                "error_message": str(err),
            }

    # 2. Map Lane Execution
    if should_route_map:
        try:
            results["map_lane"] = map_lane.handle(message)
        except Exception as err:
            logger.exception("Map lane error on message")
            results["map_lane"] = {
                "status": "error",
                "error_type": type(err).__name__,
                "error_message": str(err),
            }

    return results
