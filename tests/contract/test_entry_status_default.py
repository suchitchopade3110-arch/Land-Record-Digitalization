"""FR-EXT-06/07 — Extraction.entry_status defaults to 'unknown', NEVER
'live', as a row default. Schema-level safety property, not a UI concern
(API-Contracts-and-Interfaces.md §3.2 contract note)."""
import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "schemas"


def test_entry_status_default_is_unknown():
    schema = json.loads((SCHEMAS_DIR / "extraction.schema.json").read_text())
    assert schema["properties"]["entry_status"]["default"] == "unknown"


def test_entry_status_enum_includes_all_states():
    schema = json.loads((SCHEMAS_DIR / "extraction.schema.json").read_text())
    assert set(schema["properties"]["entry_status"]["enum"]) == {
        "unknown", "live", "cancelled", "superseded", "amended",
    }
