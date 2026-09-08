"""Validates this service's Extraction messages against
contracts/schemas/extraction.schema.json before every deploy. TODO: fill in
once backend actually writes Extraction rows (it owns the table, Shree
writes most fields)."""
import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "contracts" / "schemas" / "extraction.schema.json"


def test_schema_file_exists():
    assert SCHEMA_PATH.exists(), "contracts/schemas/extraction.schema.json must exist"


def test_schema_is_valid_json():
    json.loads(SCHEMA_PATH.read_text())


@pytest.mark.skip(reason="TODO: validate real Extraction rows once backend's DB layer exists")
def test_extraction_rows_conform():
    ...
