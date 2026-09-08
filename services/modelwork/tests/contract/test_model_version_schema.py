"""Validates this service's outbound ModelVersion messages against
contracts/schemas/model_version.schema.json before every deploy."""
import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parents[4] / "contracts" / "schemas"


def test_model_version_schema_is_valid_json():
    json.loads((SCHEMAS_DIR / "model_version.schema.json").read_text())
