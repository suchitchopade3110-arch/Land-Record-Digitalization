"""Reads config (unit tables, gazetteer closed sets) from Suchit's Config Service (FR-CFG-02)."""
from __future__ import annotations

import os
import httpx
from observability import ConfigClient

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


def _fetch_over_http(scope: str, key: str) -> dict:
    """Fetch configuration from backend GET /config/{scope}/{key}."""
    url = f"{BACKEND_URL}/config/{scope}/{key}"
    try:
        response = httpx.get(url, timeout=5.0)
        if response.status_code == 200:
            return response.json()
    except Exception:
        pass
    # Fallback default configuration if config service endpoint is unavailable
    return {"scope": scope, "key": key, "value": "default"}


config_client = ConfigClient(fetch=_fetch_over_http)
