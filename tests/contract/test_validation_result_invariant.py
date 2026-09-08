"""FR-VAL-09 — consumed_constraints[] non-empty MUST force verdict ==
not_applicable. This is a schema-level invariant, not a convention
(API-Contracts-and-Interfaces.md §3.4). See also
infra/migrations/versions/0001_validation_result_not_applicable_trigger.py
for the DB-enforced version of the same rule, and
services/validation/tests/contract/test_validation_result_invariant.py for
the unit-level check against Shruthi's pure function.
"""
import json
from pathlib import Path

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "schemas"


def test_schema_encodes_the_invariant():
    schema = json.loads((SCHEMAS_DIR / "validation_result.schema.json").read_text())
    all_of = schema.get("allOf", [])
    assert any(
        clause.get("then", {}).get("properties", {}).get("verdict", {}).get("const") == "not_applicable"
        for clause in all_of
    ), "validation_result.schema.json must force verdict=not_applicable when consumed_constraints is non-empty"
