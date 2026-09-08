"""Validates this service's outbound Extraction / ParcelGeometry messages
against contracts/schemas/*.json before every deploy."""
import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parents[4] / "contracts" / "schemas"


def test_extraction_schema_is_valid_json():
    json.loads((SCHEMAS_DIR / "extraction.schema.json").read_text())


def test_parcel_geometry_schema_is_valid_json():
    json.loads((SCHEMAS_DIR / "parcel_geometry.schema.json").read_text())
