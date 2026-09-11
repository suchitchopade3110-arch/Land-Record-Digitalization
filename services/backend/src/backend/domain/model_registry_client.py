"""HTTP client for Tharun's Model Registry (services/modelwork).
FR-TRI-09, FR-LRN-04, API-Contracts §4.2.

Resolves active promoted model versions to pin into WorkEnvelope at M2 triage.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

# Transport config (D3)
# Config key: model_registry.base_url (env: MODEL_REGISTRY_BASE_URL)
DEFAULT_MODEL_REGISTRY_BASE_URL = "http://localhost:8004"

# Config key: model_registry.timeout_ms
DEFAULT_MODEL_REGISTRY_TIMEOUT_MS = 2000


class ModelRegistryResolutionError(RuntimeError):
    """Raised when active model versions cannot be resolved from the Model Registry (D1)."""


# Mapping from Model Registry module name (model_version.schema.json) to
# WorkEnvelope model_versions key (work_envelope.schema.json).
# Both schemas are frozen per Ground Rule 1.
MODULE_TO_ENVELOPE_KEY: dict[str, str] = {
    "printed_ocr": "printed_ocr",
    "hwr": "hwr",
    "calibrator": "confidence_calibrator",
    "novelty_detector": "novelty_detector",
    "triage_classifier": "triage_classifier",
}


def resolve_active_model_versions(
    *,
    base_url: str | None = None,
    timeout_ms: int = DEFAULT_MODEL_REGISTRY_TIMEOUT_MS,
    client: httpx.Client | None = None,
) -> dict[str, str]:
    """Resolve currently promoted model versions for all 5 required envelope modules.

    D1 failure policy: If any module is unreachable, times out, or returns != 200 (including 404),
    raise ModelRegistryResolutionError. No partial envelopes or silent defaults.
    D2 stratum: Does not send stratum at page-level triage.
    """
    resolved_versions: dict[str, str] = {}
    url_base = base_url or os.environ.get("MODEL_REGISTRY_BASE_URL", DEFAULT_MODEL_REGISTRY_BASE_URL)
    timeout_sec = timeout_ms / 1000.0

    owns_client = client is None
    http_client = client or httpx.Client(base_url=url_base, timeout=timeout_sec)

    try:
        for module, envelope_key in MODULE_TO_ENVELOPE_KEY.items():
            try:
                resp = http_client.get(f"/models/{module}/active")
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                raise ModelRegistryResolutionError(
                    f"Failed to query model registry for module '{module}': {exc}"
                ) from exc

            if resp.status_code != 200:
                raise ModelRegistryResolutionError(
                    f"Model registry returned HTTP {resp.status_code} for module '{module}': {resp.text}"
                )

            data: dict[str, Any] = resp.json()
            model_ver = data.get("model_version")
            if not model_ver or not isinstance(model_ver, str):
                raise ModelRegistryResolutionError(
                    f"Model registry returned invalid or missing model_version for module '{module}': {data}"
                )

            resolved_versions[envelope_key] = model_ver

        return resolved_versions
    finally:
        if owns_client:
            http_client.close()
