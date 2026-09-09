# ADR-001 — Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2

## Status
Accepted.

## Context
Four people build against each other's contracts, not each other's code
(`API-Contracts-and-Interfaces.md`). If the four services are in different
languages, every contract needs a marshalling layer on at least one side,
and `contracts/generated/python` stops being "the" generated package — it
becomes one of several.

## Decision
All four services (`services/backend`, `services/extraction`,
`services/validation`, `services/modelwork`) are Python 3.11, FastAPI for
the synchronous API surface (§4 of the contract doc — config service, model
registry, closed-set lookup), SQLAlchemy 2.0 for persistence, Alembic for
migrations (single history, `infra/migrations/`), and Pydantic v2 for every
schema — both the contract-generated models in
`contracts/generated/python/` and each service's own request/response
models.

## Consequences
- One type system: a `contracts/schemas/*.json` change regenerates one
  Pydantic package every service imports directly. No JSON-Schema-to-X
  codegen per language, no drift between "the Python types" and "the Go
  types."
- Alembic's single migration history across all four services' tables is
  what makes `infra/CODEOWNERS`' schema sign-off rule enforceable — a
  migration touching another team's table is visible in the diff, not
  buried in a per-service migration directory nobody else reads.
- FastAPI's OpenAPI generation is what `gateway/route_registry.yaml`'s CI
  drift check (`.github/workflows/ci.yml`, `route-registry-check` job) will
  eventually diff against, once services boot in CI.
- Cost: if a 5th service or a partner integration is not Python, it talks
  to this system only through the frozen contracts (queue messages, the
  three synchronous APIs) — never by importing `contracts_generated`
  directly. That boundary is already how `libs/` is meant to be consumed,
  so this is not a new constraint, just a named one.
