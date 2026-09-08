"""Cross-service contract test: the WorkEnvelope pinned at triage
(API-Contracts §2, FR-TRI-09) is read-only downstream. A retried message
after a model promotion must reproduce its original result, never a new one.

TODO: this currently only validates the schema shape. Extend once
services/backend's triage_router and a real broker exist, to replay a
message under two different "active" model registrations and assert
byte-identical output.
"""
import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "schemas"


def test_work_envelope_schema_is_valid_json():
    schema = json.loads((SCHEMAS_DIR / "work_envelope.schema.json").read_text())
    assert schema["required"] == ["envelope_id", "document_id", "page_id", "pinned_at", "model_versions", "config_version"]


def test_work_envelope_pins_all_five_model_versions():
    schema = json.loads((SCHEMAS_DIR / "work_envelope.schema.json").read_text())
    model_versions_props = schema["properties"]["model_versions"]["properties"]
    assert set(model_versions_props) == {
        "triage_classifier", "printed_ocr", "hwr", "confidence_calibrator", "novelty_detector",
    }
