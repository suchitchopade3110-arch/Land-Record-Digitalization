"""Unit tests for text_lane and triage_consumer workers."""
from extraction.workers import text_lane, triage_consumer


def test_text_lane_handle():
    message = {
        "message_id": "msg-123",
        "trace_id": "doc-1:page-1",
        "work_envelope": {"config_version": "v1"},
        "payload": {
            "page_id": "page-123",
            "document_id": "doc-123",
            "doc_type": "ror",
            "page_role": "text",
            "image_bytes": b"fake_image_content",
        },
    }

    res = text_lane.handle(message)
    assert res["status"] == "success"
    assert res["extractions_count"] > 0
    assert "text_lane_envelope" in res
    assert "assembly_envelope" in res

    # Verify outbound envelopes are valid
    extractions = res["text_lane_envelope"]["payload"]
    for item in extractions:
        assert item["entry_status"] == "unknown"


def test_triage_consumer_handle():
    message = {
        "message_id": "msg-456",
        "trace_id": "doc-2:page-2",
        "payload": {
            "id": "page-456",
            "document_id": "doc-456",
            "doc_type": "jamabandi",
            "page_role": "tabular_register",
            "route": ["text"],
        },
    }

    res = triage_consumer.handle(message)
    assert "text_lane" in res
    assert res["text_lane"]["status"] == "success"
