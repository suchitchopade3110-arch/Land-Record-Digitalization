.PHONY: up down install migrate migrate-libtest test-db seed lint test test-unit test-contract test-invariant test-property test-e2e verify

# Matches infra/docker-compose.yml's postgres service as seen from the
# host (postgres/dev@localhost:5432/landrecords). Override for a
# non-docker Postgres (e.g. a native local cluster) or CI.
DATABASE_URL      ?= postgresql+psycopg://postgres:dev@localhost:5432/landrecords
TEST_DATABASE_URL ?= postgresql+psycopg://postgres:dev@localhost:5432/landrecords_test
LIB_TEST_DATABASE_URL ?= postgresql+psycopg://postgres:dev@localhost:5432/landrecords_libtest
QUEUE_URL         ?= redis://localhost:6379/0

# Superuser used only by `test-db` to drop/create the two throwaway test
# databases — override on a host where the docker-compose default
# (postgres/dev) isn't the right admin credential, e.g.:
#   make test-db PSQL_SUPERUSER=dev PSQL_SUPERUSER_PGPASSWORD=dev
PSQL_SUPERUSER          ?= postgres
PSQL_SUPERUSER_PGPASSWORD ?= dev
PSQL_HOST ?= localhost

LIBS = libs/observability libs/queue libs/storage libs/outbox libs/envelope libs/masking libs/audit
SERVICES = services/backend services/extraction services/validation services/modelwork

# ---- local dev environment (docker) ----

up: ## Bring up postgres+postgis, redis, minio, all 4 services, gateway.
	cd infra && docker compose up -d

down:
	cd infra && docker compose down

# ---- install ----

install: ## Editable-install every lib and service into the current Python env.
	pip install -e $(LIBS) $(SERVICES)
	pip install pytest jsonschema pyyaml ruff  # test/lint tooling, matching .github/workflows/*.yml

# ---- schema ----

migrate: ## Apply infra/migrations against $DATABASE_URL (default: the docker-compose Postgres).
	cd infra/migrations && DATABASE_URL="$(DATABASE_URL)" python -m alembic upgrade head

test-db: ## (Re)create landrecords_test + landrecords_libtest and migrate the former.
	@echo "Dropping/recreating landrecords_test and landrecords_libtest..."
	PGPASSWORD="$(PSQL_SUPERUSER_PGPASSWORD)" dropdb --if-exists -h $(PSQL_HOST) -U $(PSQL_SUPERUSER) landrecords_test
	PGPASSWORD="$(PSQL_SUPERUSER_PGPASSWORD)" createdb -h $(PSQL_HOST) -U $(PSQL_SUPERUSER) landrecords_test
	PGPASSWORD="$(PSQL_SUPERUSER_PGPASSWORD)" dropdb --if-exists -h $(PSQL_HOST) -U $(PSQL_SUPERUSER) landrecords_libtest
	PGPASSWORD="$(PSQL_SUPERUSER_PGPASSWORD)" createdb -h $(PSQL_HOST) -U $(PSQL_SUPERUSER) landrecords_libtest
	cd infra/migrations && DATABASE_URL="$(TEST_DATABASE_URL)" python -m alembic upgrade head

seed: ## Load a small fake batch/document/page so the API has something to show.
	DATABASE_URL="$(DATABASE_URL)" python scripts/seed.py

# ---- quality gates ----

lint:
	ruff check $(LIBS:%=%/src) $(SERVICES:%=%/src)

test-unit:
	for svc in $(SERVICES); do python -m pytest $$svc/tests/unit -v || exit 1; done
	python -m pytest libs/observability/tests -v

test-contract: ## Cross-service contract tests + each service's own (needs a migrated $TEST_DATABASE_URL).
	TEST_DATABASE_URL="$(TEST_DATABASE_URL)" python -m pytest tests/contract -v
	for svc in $(SERVICES); do TEST_DATABASE_URL="$(TEST_DATABASE_URL)" python -m pytest $$svc/tests/contract -v || exit 1; done

test-invariant: ## The four §5 invariants — proves the violating write fails, against a real DB.
	TEST_DATABASE_URL="$(TEST_DATABASE_URL)" python -m pytest tests/invariant -v

test-property:
	python -m pytest tests/property -v

test-e2e: ## The fake end-to-end pipeline run (Phase 1 gate) — real Postgres + real Redis.
	TEST_DATABASE_URL="$(TEST_DATABASE_URL)" python -m pytest tests/e2e -v

test-libs: ## Each lib's own unit/integration tests (isolated on $LIB_TEST_DATABASE_URL — never $TEST_DATABASE_URL, see libs/audit/tests/test_chain.py's docstring).
	LIB_TEST_DATABASE_URL="$(LIB_TEST_DATABASE_URL)" python -m pytest libs/queue/tests libs/storage/tests libs/masking/tests libs/envelope/tests libs/outbox/tests libs/audit/tests -v

test: test-unit test-libs test-contract test-invariant test-property test-e2e

verify: install lint test-db test ## The Phase 1 gate: schema conformance + a fake end-to-end run (test already includes test-e2e).
	@echo ""
	@echo "make verify: green."
