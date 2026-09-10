"""Validates this service's outbound Extraction / ParcelGeometry / RecordAssembly messages
against contracts/schemas/*.json before every deploy.
"""
import json
from pathlib import Path
from extraction.workers.text_lane import handle as handle_text_lane

import jsonschema

SCHEMAS_DIR = Path(__file__).resolve().parents[4] / "contracts" / "schemas"


def test_extraction_schema_is_valid_json():
    schema = json.loads((SCHEMAS_DIR / "extraction.schema.json").read_text())
    assert schema["title"] == "Extraction"


def test_parcel_geometry_schema_is_valid_json():
    schema = json.loads((SCHEMAS_DIR / "parcel_geometry.schema.json").read_text())
    assert schema["title"] == "ParcelGeometry"


def test_generated_extraction_matches_contract_schema():
    extraction_schema = json.loads((SCHEMAS_DIR / "extraction.schema.json").read_text())
    assembly_schema = json.loads((SCHEMAS_DIR / "record_assembly.schema.json").read_text())

    message = {
        "message_id": "msg-001",
        "trace_id": "doc-001:page-001",
        "work_envelope": {
            "envelope_id": "00000000-0000-0000-0000-000000000001",
            "document_id": "00000000-0000-0000-0000-000000000002",
            "page_id": "00000000-0000-0000-0000-000000000003",
            "pinned_at": "2026-09-10T10:00:00Z",
            "model_versions": {
                "triage_classifier": "stub-v0",
                "printed_ocr": "baidu-unlimited-v1",
                "hwr": "hwr-v1",
                "confidence_calibrator": "stub-v0",
                "novelty_detector": "stub-v0",
            },
            "config_version": "v1",
        },
        "payload": {
            "page_id": "00000000-0000-0000-0000-000000000003",
            "document_id": "00000000-0000-0000-0000-000000000002",
            "doc_type": "ror",
            "page_role": "text",
        },
    }

    res = handle_text_lane(message)
    extractions = res["text_lane_envelope"]["payload"]
    record_assembly = res["assembly_envelope"]["payload"]["record_assembly"]

    # Validate extractions against extraction.schema.json
    for ext in extractions:
        jsonschema.validate(instance=ext, schema=extraction_schema)
        # Check hard invariant: entry_status MUST default to unknown
        assert ext["entry_status"] == "unknown"

    # Validate record_assembly against record_assembly.schema.json
    jsonschema.validate(instance=record_assembly, schema=assembly_schema)
