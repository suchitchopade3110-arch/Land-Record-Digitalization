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
