# Phase 6 — P0 closeout

## T1-01 · M15 access control — RBAC on the Phase 2/3 routes, actor from identity, maker-checker confirm route

### 1. Decisions Applied
- **D1 permission grants:**
  - `DOCUMENT_INGEST` (`document.ingest`): operator, administrator → `POST /documents`
  - `REVIEW_CLAIM` (`review.claim`): operator, verifier → `GET /review-tasks`, `POST /review-tasks/next`, `GET /review-tasks/{task_id}/crop`
  - `REVIEW_SUBMIT` (`review.submit`): operator, verifier → `POST /review-tasks/{task_id}/submit`, `POST /review-tasks/{task_id}/skip`
  - `REVIEW_CONFIRM` (`review.confirm`): verifier, supervisor → `POST /review-tasks/pending-corrections/{pending_id}/confirm`
  - `CONFLICT_READ` (`conflict.read`): verifier, supervisor, auditor → `GET /conflicts`, `GET /conflicts/{conflict_id}`
  - `CONFLICT_ASSIGN` (`conflict.assign`): supervisor → `POST /conflicts/{conflict_id}/assign`
  - `CONFLICT_TRANSITION` (`conflict.transition`): supervisor → `POST /conflicts/{conflict_id}/transition`
  - `DASHBOARD_READ` (`dashboard.read`): supervisor, auditor, administrator → `GET /dashboard/metrics`
  - `GET /config/{scope}/{key}` and `GET /closed-sets/{type}` stay unauthenticated service reads (no personal data), declared in `gateway/route_registry.yaml` with `permission: service_read_no_personal_data`.
  - Bumped `PERMISSION_MATRIX_VERSION` to `"phase6-t1-01-v1"`.
- **D2 confirmer rule:**
  - Confirmer ≠ the correction's maker (`pending.first_actor`), Confirmer ≠ the review task's claimant (`task.assignee`), and Confirmer holds `REVIEW_CONFIRM`.
  - A violation returns 403 (with `SameActorCannotConfirm`) and logs `action="correction.confirm_rejected"` in audit.
  - Enforced in domain layer (`backend.domain.correction.confirm_pending_correction`).
- **D3 confirm route path:**
  - Mounted `POST /review-tasks/pending-corrections/{pending_id}/confirm` under the existing `/review-tasks` router.

### 2. Verification
- `tests/contract/test_route_registry_permissions.py`: All mounted routes × all 5 roles verified against declared `permission:`.
- `tests/contract/test_actor_spoofing_and_workflow_rbac.py`: Actor spoofing via body/query parameters prevented; `identity.actor` used for all operations.
- `tests/contract/test_rbac_audit_shard_and_masking.py`: `rbac.checked` and `rbac.denied` entries land in `landaudit.SYSTEM_SHARD_KEY` with no field values.

## T1-02 · Queue infrastructure — Worker runner: consume loops, ack-after-commit, dead-letter, outbox relay loop

### 1. Decisions Applied
- **D1 delivery semantics:**
  - At-least-once. Messages are acknowledged (`queue.ack`) only after the handler's database transaction commits (`ack-after-commit`).
  - Handler exceptions roll back the database session and leave the message unacknowledged for broker redelivery.
  - After `queue.max_deliveries` (default 5, configurable), poison messages are moved to `<QUEUE>.DLQ`, acknowledged on the main stream, and audited via `queue.dead_lettered` in `landaudit.SYSTEM_SHARD_KEY` with `queue`, `message_id`, `delivery_count`, and `error_class` (no payload values).
  - Parity gap closed in `libs/queue`: `RedisStreamsQueue` and `InMemoryQueue` both check and redeliver pending unacked messages before reading new stream messages.
- **D2 transaction ownership:**
  - `WorkerRunner` owns the session lifecycle and single transaction commit.
  - Handlers (`triage_router`, `decision_engine`, `ingestion_consumer`) flush changes without calling `session.commit()` directly.
- **D3 process layout:**
  - Single entrypoint `python -m backend.workers.run <worker>` supporting `ingestion`, `triage`, `decision`, and `outbox-relay`.
  - Docker Compose services defined: `backend-ingestion`, `backend-triage`, `backend-decision`, `backend-outbox-relay`.
  - Consumer group: `backend.<worker>`; consumer name: `<hostname>-<pid>`.
  - `make workers` target added to `Makefile` to bring up worker containers.
- **D4 config keys:**
  - Defined in `backend.domain.queue_policy` with config keys named next to each default:
    - `queue.block_ms` (`DEFAULT_BLOCK_MS = 1000`)
    - `queue.batch_count` (`DEFAULT_BATCH_COUNT = 1`)
    - `queue.max_deliveries` (`DEFAULT_MAX_DELIVERIES = 5`)
    - `outbox.relay_interval_ms` (`DEFAULT_RELAY_INTERVAL_MS = 500`)
    - `outbox.relay_batch_size` (`DEFAULT_RELAY_BATCH_SIZE = 100`)

### 2. Verification
- `tests/queue/test_worker_runner.py`:
  - `test_ack_after_commit_redelivers_on_failure`
  - `test_crash_between_commit_and_ack_is_idempotent_ingestion`
  - `test_crash_between_commit_and_ack_is_idempotent_triage`
  - `test_poison_message_lands_in_dlq_after_max_deliveries`
  - `test_two_relay_loops_drain_outbox_without_duplicates`
  - `test_sigterm_mid_batch_finishes_or_rolls_back_safely`
  - `test_block_ms_from_config_reaches_driver_call`
- `tests/queue/test_conformance.py`: 9 passed against `InMemoryQueue` (Redis/NATS skipped if servers not running locally).
- `infra/docker-compose.yml`: `docker compose config` validates cleanly with all new worker services.

