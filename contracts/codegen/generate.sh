#!/usr/bin/env bash
# TODO: contracts/generated/python is the only import path for entity models —
# no service may hand-redefine a schema. Regenerate and commit whenever
# contracts/schemas/*.json changes (CODEOWNERS gates that PR on the joint
# owners named in each schema file's description).
#
# Turns contracts/schemas/*.json into pydantic models under
# contracts/generated/python/, importable by all four services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTRACTS_DIR="$(dirname "$SCRIPT_DIR")"
SCHEMAS_DIR="$CONTRACTS_DIR/schemas"
OUT_DIR="$CONTRACTS_DIR/generated/python/contracts_generated"

command -v datamodel-codegen >/dev/null 2>&1 || {
  echo "datamodel-codegen not found. Install with: pip install datamodel-code-generator" >&2
  exit 1
}

mkdir -p "$OUT_DIR"
: > "$OUT_DIR/__init__.py"

for schema in "$SCHEMAS_DIR"/*.schema.json; do
  name="$(basename "$schema" .schema.json)"
  echo "generating ${name}.py from $(basename "$schema")"
  datamodel-codegen \
    --input "$schema" \
    --input-file-type jsonschema \
    --output "$OUT_DIR/${name}.py" \
    --target-python-version 3.11 \
    --use-schema-description \
    --class-name "$(python3 -c "print(''.join(w.capitalize() for w in '${name}'.split('_')))")"
done

echo "Generated pydantic models in $OUT_DIR — import as 'from contracts_generated.<entity> import <Model>'"
