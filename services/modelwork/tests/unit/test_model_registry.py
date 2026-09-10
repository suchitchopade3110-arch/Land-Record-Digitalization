"""Unit tests for the Active Model Registry API (FR-LRN-04)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from modelwork.api.model_registry import (
    VALID_MODULES,
    _ACTIVE_PROMOTED_MODELS,
    _WRITER_ADAPTERS,
    ActiveModelRecord,
    get_active_model,
)
from modelwork.main import app

try:
    from fastapi.testclient import TestClient
    client = TestClient(app)
except Exception:
    client = None


@pytest.fixture(autouse=True)
def isolate_registry_state():
    """Ensure every test runs with an isolated copy of the in-process registry state."""
    saved_models = dict(_ACTIVE_PROMOTED_MODELS)
    saved_adapters = dict(_WRITER_ADAPTERS)
    try:
        yield
    finally:
        _ACTIVE_PROMOTED_MODELS.clear()
        _ACTIVE_PROMOTED_MODELS.update(saved_models)
        _WRITER_ADAPTERS.clear()
        _WRITER_ADAPTERS.update(saved_adapters)


def test_get_active_model_valid_modules():
    """Test 1: Every valid module returns 200, string model_version, and valid adapter_ref."""
    expected_modules = {
        "printed_ocr",
        "hwr",
        "calibrator",
        "novelty_detector",
        "triage_classifier",
    }
    assert VALID_MODULES == expected_modules

    for module in sorted(expected_modules):
        # Direct function call verification
        result = get_active_model(module=module)
        assert "model_version" in result
        assert isinstance(result["model_version"], str)
        assert len(result["model_version"]) > 0
        assert "adapter_ref" in result
        assert result["adapter_ref"] is None or isinstance(result["adapter_ref"], str)

        # HTTP route verification via TestClient if available
        if client is not None:
            response = client.get(f"/models/{module}/active")
            assert response.status_code == 200
            payload = response.json()
            assert "model_version" in payload
            assert isinstance(payload["model_version"], str)
            assert "adapter_ref" in payload
            assert payload["adapter_ref"] is None or isinstance(payload["adapter_ref"], str)


def test_get_active_model_with_writer_cluster():
    """Test 2: Call with valid UUID writer_cluster_id produces a contract-compatible response."""
    test_cluster_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    # Direct function call
    result = get_active_model(module="hwr", writer_cluster_id=test_cluster_uuid)
    assert "model_version" in result
    assert isinstance(result["model_version"], str)
    assert "adapter_ref" in result
    # Unconfigured cluster returns None (null in JSON)
    assert result["adapter_ref"] is None

    # HTTP route verification via TestClient
    if client is not None:
        response = client.get(f"/models/hwr/active?writer_cluster_id={test_cluster_uuid}")
        assert response.status_code == 200
        payload = response.json()
        assert "model_version" in payload
        assert isinstance(payload["model_version"], str)
        assert payload["adapter_ref"] is None


def test_get_active_model_unknown_module_returns_404():
    """Test 3: An unknown module raises 404 with exact message."""
    unknown_module = "unknown_module"

    # Direct function call
    with pytest.raises(HTTPException) as exc_info:
        get_active_model(module=unknown_module)
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == f"No promoted model for module '{unknown_module}'"

    # HTTP route verification via TestClient
    if client is not None:
        response = client.get(f"/models/{unknown_module}/active")
        assert response.status_code == 404
        assert response.json()["detail"] == f"No promoted model for module '{unknown_module}'"


def test_get_active_model_unpromoted_valid_module_returns_404():
    """A valid module with no active promoted model raises 404."""
    _ACTIVE_PROMOTED_MODELS.pop("calibrator", None)

    with pytest.raises(HTTPException) as exc_info:
        get_active_model(module="calibrator")
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "No promoted model for module 'calibrator'"

    if client is not None:
        response = client.get("/models/calibrator/active")
        assert response.status_code == 404
        assert response.json()["detail"] == "No promoted model for module 'calibrator'"


def test_get_active_model_with_stratum():
    """Stratum query parameter is accepted per contract specification."""
    stratum = "owner_name|devanagari|handwriting|good|cluster_1"
    result = get_active_model(module="calibrator", stratum=stratum)
    assert "model_version" in result
    assert isinstance(result["model_version"], str)
    assert "adapter_ref" in result

    if client is not None:
        response = client.get(f"/models/calibrator/active?stratum={stratum}")
        assert response.status_code == 200
        payload = response.json()
        assert "model_version" in payload
        assert "adapter_ref" in payload


def test_get_active_model_with_configured_writer_adapter():
    """Configured adapter reference for a writer cluster is resolved and returned."""
    cluster_id = "00000000-0000-0000-0000-000000000001"
    adapter_id = "11111111-1111-1111-1111-111111111111"
    _WRITER_ADAPTERS[("hwr", cluster_id)] = adapter_id

    result = get_active_model(module="hwr", writer_cluster_id=cluster_id)
    assert result["adapter_ref"] == adapter_id

    if client is not None:
        response = client.get(f"/models/hwr/active?writer_cluster_id={cluster_id}")
        assert response.status_code == 200
        assert response.json()["adapter_ref"] == adapter_id
