"""Unit tests for HWR Abstraction & Placeholder Adapter (FR-OCR-02 / FR-TRI-11).

Tests:
1. HWREngineAdapter interface inheritance and region processing signature
2. Region bounding box preservation in HWR result
3. Normalized OCRResult fields (text, bbox, confidence=0.0, engine='hwr', model_version, config_version)
4. Documented unsupported placeholder result (no fake OCR text)
5. Explicit failure with HWRNotImplementedError when configured
6. Per-writer adapter selection and WriterAwareHWRRegistry
"""
import pytest
from extraction.domain.ocr.hwr_adapter import HWRAdapter, WriterAwareHWRRegistry
from extraction.domain.ocr.interfaces import (
    BoundingBox,
    HWREngineAdapter,
    HWRNotImplementedError,
    OCRResult,
)


def test_hwr_adapter_inherits_interface():
    adapter = HWRAdapter()
    assert isinstance(adapter, HWREngineAdapter)
    assert adapter.engine_id == "hwr"
    assert adapter.model_version == "hwr-placeholder-v1"


def test_hwr_adapter_process_region_normalized_result():
    adapter = HWRAdapter()
    region_bbox = BoundingBox(x=15.0, y=250.0, w=300.0, h=40.0)

    res = adapter.process_region(
        image_bytes=b"fake_handwritten_bytes",
        page_id="page-hwr-001",
        region_bbox=region_bbox,
        config_version="v2",
    )

    assert isinstance(res, OCRResult)
    assert res.page_id == "page-hwr-001"
    assert res.engine_id == "hwr"
    assert res.model_version == "hwr-placeholder-v1"
    assert res.config_version == "v2"
    assert res.full_text == "[UNSUPPORTED_HWR_REGION]"

    # Check token preservation
    assert len(res.tokens) == 1
    token = res.tokens[0]
    assert token.text == "[UNSUPPORTED_HWR_REGION]"
    assert token.confidence == 0.0  # Documented unsupported (no fake confidence)
    assert token.bbox == region_bbox


def test_hwr_adapter_explicit_failure_when_configured():
    adapter = HWRAdapter(raise_on_unsupported=True)
    region_bbox = BoundingBox(x=10.0, y=10.0, w=100.0, h=100.0)

    with pytest.raises(HWRNotImplementedError) as exc_info:
        adapter.process_region(
            image_bytes=b"bytes",
            page_id="page-fail-002",
            region_bbox=region_bbox,
            writer_cluster_id="cluster-99",
        )

    assert "Production HWR engine is not configured" in str(exc_info.value)
    assert "cluster-99" in str(exc_info.value)


def test_per_writer_cluster_support_and_registry():
    default_adapter = HWRAdapter(model_version="hwr-default-v1")
    custom_writer_adapter = HWRAdapter(model_version="hwr-writer-cluster-42-v1")

    registry = WriterAwareHWRRegistry(default_adapter=default_adapter)
    registry.register_writer_adapter(writer_cluster_id="writer-cluster-42", adapter=custom_writer_adapter)

    # Fetch default adapter
    adapter_default = registry.get_adapter(writer_cluster_id=None)
    assert adapter_default.model_version == "hwr-default-v1"

    # Fetch cluster-specific adapter
    adapter_custom = registry.get_adapter(writer_cluster_id="writer-cluster-42")
    assert adapter_custom.model_version == "hwr-writer-cluster-42-v1"
