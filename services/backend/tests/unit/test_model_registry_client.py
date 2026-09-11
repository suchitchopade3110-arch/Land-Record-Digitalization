"""Unit tests for backend.domain.model_registry_client (FR-TRI-09, FR-LRN-04)."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from backend.domain.model_registry_client import (
    MODULE_TO_ENVELOPE_KEY,
    ModelRegistryResolutionError,
    resolve_active_model_versions,
)

SCHEMAS_DIR = Path(__file__).resolve().parents[4] / "contracts" / "schemas"
WORK_ENVELOPE_SCHEMA = json.loads((SCHEMAS_DIR / "work_envelope.schema.json").read_text(encoding="utf-8"))


def test_resolve_active_model_versions_mapping_and_schema():
    """Verify all 5 modules are resolved and mapped (calibrator -> confidence_calibrator)."""
    responses = {
        "/models/printed_ocr/active": {"model_version": "printed_ocr_v1", "adapter_ref": None},
        "/models/hwr/active": {"model_version": "hwr_v1", "adapter_ref": None},
        "/models/calibrator/active": {"model_version": "calibrator_v1", "adapter_ref": None},
        "/models/novelty_detector/active": {"model_version": "novelty_detector_v1", "adapter_ref": None},
        "/models/triage_classifier/active": {"model_version": "triage_classifier_v1", "adapter_ref": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            return httpx.Response(200, json=responses[path])
        return httpx.Response(404, json={"detail": "Not found"})

    client = httpx.Client(base_url="http://test-registry", transport=httpx.MockTransport(handler))
    resolved = resolve_active_model_versions(client=client)

    assert resolved == {
        "printed_ocr": "printed_ocr_v1",
        "hwr": "hwr_v1",
        "confidence_calibrator": "calibrator_v1",
        "novelty_detector": "novelty_detector_v1",
        "triage_classifier": "triage_classifier_v1",
    }
    assert set(resolved.keys()) == set(WORK_ENVELOPE_SCHEMA["properties"]["model_versions"]["required"])


def test_resolve_active_model_versions_raises_on_404():
    """D1 failure policy: 404 for any module raises ModelRegistryResolutionError."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "calibrator" in request.url.path:
            return httpx.Response(404, json={"detail": "calibrator not found"})
        return httpx.Response(200, json={"model_version": "v1"})

    client = httpx.Client(base_url="http://test-registry", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelRegistryResolutionError, match="calibrator"):
        resolve_active_model_versions(client=client)


def test_resolve_active_model_versions_raises_on_timeout_or_connection_error():
    """D1 failure policy: connection errors/timeouts raise ModelRegistryResolutionError."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = httpx.Client(base_url="http://test-registry", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelRegistryResolutionError, match="Connection refused"):
        resolve_active_model_versions(client=client)


def test_resolve_active_model_versions_raises_on_malformed_response():
    """Verify resolver raises when model_version is missing or not a string."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model_version": None})

    client = httpx.Client(base_url="http://test-registry", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelRegistryResolutionError, match="invalid or missing model_version"):
        resolve_active_model_versions(client=client)
