"""P4-10b — the live route×role masking property test (FR-SEC-02, ADR-007).

`tests/property/test_masking_never_leaks_across_routes.py` and
`tests/contract/test_route_registry_masking.py` both check *static*
shape: the registry file declares a masking treatment for a path, and the
response *models* have no field that bypasses the policy function.
Neither ever drives a live request through the app. Phase 4 was marked
done on the strength of those two checks alone. This is the missing live
half: enumerate every route FastAPI actually mounted
(`app.routes`, not a hand-kept list), cross with all five roles, and
prove no *response* leaks a seeded personal-data value.

Building this test found a real, pre-existing leak — fixed in the same
commit, not just documented: `GET /extractions/{id}/provenance` built its
`edit_history` dict by hand instead of calling the already-built
`mask_edit_history_entry` serializer, so a personal-data field's
corrected/predicted history was returned unmasked to any role holding
`PROVENANCE_READ`. See `backend.api.records.get_provenance`'s updated
docstring for the fix.

Real Postgres via `TEST_DATABASE_URL`, skipped if the Phase 4 schema
(0005, `field_provenance`/`chain_root`) isn't migrated — same convention
as `services/backend/tests/contract/test_phase4_publication_provenance_audit.py`.
"""
from __future__ import annotations

import os
import re

import pytest
from backend.domain.correction import submit_correction
from backend.domain.provenance import record_provenance
from backend.domain.publication import publish_new_version
from backend.main import app
from backend.models.entities import (
    Batch,
    Extraction,
    Page,
    RecordAssembly,
    SourceDocument,
)
from landmasking import MASK_TOKEN
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords_test"
)

ROLE_ACTORS = {
    "operator": "operator1",
    "verifier": "verifier1",
    "supervisor": "supervisor1",
    "auditor": "auditor1",
    "administrator": "admin1",
}

# Two distinct personal-data strings — the raw/current value, and a small-
# edit-distance correction of it (stays a direct `Correction` row rather
# than `PendingCorrection`, per MAKER_CHECKER_EDIT_DISTANCE_THRESHOLD, so
# it appears in `edit_history`). Both are deliberately unlike any string
# that occurs anywhere else in this schema/test suite, so any appearance
# in a response body is unambiguous evidence of a leak, not a coincidence.
SECRET_VALUE = "Zzq9SecretOwnerRawValue831"
SECRET_CORRECTED = "Zzq9SecretOwnerRawValue832"


# ---------------------------------------------------------------------------
# Route enumeration — computed from the live app's own route table, not a
# hand-kept list, per P4-10b's own requirement: a new route is picked up
# automatically and fails by default (the sweep test below has no
# allowlist, only a short, justified exclusion list).
#
# `app.routes` itself is unusable for this: this FastAPI version keeps
# each `include_router()` call as a lazily-resolved `_IncludedRouter`
# wrapper (not individual `APIRoute`s) until something forces resolution
# — `services/backend/tests/unit/test_main.py` already documents and
# works around the exact same quirk by reading `app.openapi()["paths"]`
# instead, which is what forces that resolution. Reused here for the same
# reason: it *is* "the app's route table," just accessed the way this
# FastAPI version actually requires.
# ---------------------------------------------------------------------------


def _mounted_routes() -> list[tuple[str, str]]:
    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, methods in paths.items()
        for method in methods
        if method.upper() not in ("HEAD", "OPTIONS")
    )


ALL_ROUTES = _mounted_routes()

# Excluded from the blanket "must never return the seeded secret" sweep
# below — each for a stated reason, never because a route is inconvenient
# to drive. Report every one of these to the user in the phase report.
EXCLUDED_FROM_SWEEP: dict[tuple[str, str], str] = {
    ("POST", "/extractions/{extraction_id}/unmasked-read"): (
        "this route's entire job is to reveal an unmasked value to the one "
        "entitled role+purpose (FR-SEC-08/P4-08) — tested separately below "
        "(test_unmasked_read_route_*), where both 'the entitled role sees "
        "it' and 'nobody else does' are actually asserted, rather than "
        "being either a false failure (for auditor) or a silent pass (for "
        "everyone else) inside a blanket 'never see it' sweep."
    ),
    ("GET", "/review-tasks/{task_id}/crop"): (
        "gateway/route_registry.yaml already declares masking: "
        "officer_working_view_out_of_scope for this route — an officer's "
        "own working crop for a task they are actively reviewing is a "
        "deliberate, documented exemption (P3-04), not a leak this suite "
        "treats as a finding."
    ),
    ("POST", "/review-tasks/{task_id}/submit"): (
        "same officer-working-view exemption as the crop route directly "
        "above (gateway/route_registry.yaml)."
    ),
    ("GET", "/healthz"): "no DB access at all — structurally cannot leak personal data.",
}

SWEPT_ROUTES = [r for r in ALL_ROUTES if r not in EXCLUDED_FROM_SWEEP]
assert SWEPT_ROUTES, "route enumeration returned nothing — the app failed to mount its routers"


def _resolve_path(path: str, seeded: dict) -> str:
    """Fill in every `{param}` the live route table might contain. Known
    entities get the seeded fixture's real ids (so the route actually
    returns real data to check); everything else gets a fixed placeholder
    that is expected to 404 — a 404 still passes the leak-scan trivially
    (there is nothing in an empty/not-found response to leak), and it
    exercises the route table's structure without hand-picking which
    routes 'matter'.
    """
    values = {
        "extraction_id": seeded["extraction_id"],
        "record_group_id": seeded["record_group_id"],
        "scope": "global",
        "key": "route-masking-test-nonexistent-key",
        "type": "route-masking-test-nonexistent-type",
        "task_id": "route-masking-test-nonexistent-task",
        "conflict_id": "route-masking-test-nonexistent-conflict",
    }
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), "placeholder")), path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(TEST_DB_URL, future=True)
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1 FROM field_provenance LIMIT 0"))
            c.execute(text("SELECT 1 FROM chain_root LIMIT 0"))
    except Exception:
        pytest.skip(f"{TEST_DB_URL} has no Phase 4 (0005) migrated schema — run migrations first")
    return eng


@pytest.fixture(scope="module")
def client(engine):
    """Overrides the one shared `backend.api.deps.get_session` dependency
    — covers every route's own session *and* `require_permission`'s audit
    write (P4-09b), since nearly every route in this sweep is permission-
    gated.

    `GET /closed-sets/{type}` doesn't go through that dependency at all,
    though — `backend.config.config_client` is a process-wide singleton
    by design (one cache, not per-request), so its own DB access is
    redirected via `set_engine_override` instead (that module's docstring
    explains why this is architecturally different from the
    `Depends(get_session)` case rather than the same gap twice).

    No `DATABASE_URL` alignment needed any more for either — this fixture
    used to set it directly for the whole module; see git history / the
    P4-09b phase report for the gap that closed.
    """
    from backend import config as backend_config
    from backend.api import deps
    from starlette.testclient import TestClient

    def _override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[deps.get_session] = _override_get_session
    backend_config.set_engine_override(engine)
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(deps.get_session, None)
        backend_config.set_engine_override(None)


@pytest.fixture(scope="module")
def seeded(engine):
    """One record carrying personal data in all three places FR-SEC-02
    names: the current (canonical) value, the provenance raw_value (the
    same `Extraction` row — `mask_extraction_for_role` masks both
    together), and a linked source crop (`page.storage_uri` +
    `extraction.bbox`, present so a crop URL *could* legitimately be
    issued — proving none *is* is a dedicated test below, not just "the
    secret string doesn't appear"). A `Correction` row adds a masked
    edit-history entry, exercising the leak this block's own fix closed.
    """
    with Session(engine) as session:
        batch = Batch(district="sitapur")
        session.add(batch)
        session.flush()
        doc = SourceDocument(
            batch_id=batch.id, sha256="e" * 64, storage_uri="sha256/ee/ee/" + "e" * 64,
            mime="image/tiff", page_count=1,
        )
        session.add(doc)
        session.flush()
        page = Page(document_id=doc.id, index=0, storage_uri="sha256/ff/ff/" + "f" * 64)
        session.add(page)
        session.flush()
        extraction = Extraction(
            page_id=page.id, field_name="owner_name", raw_value=SECRET_VALUE, canonical_value=SECRET_VALUE,
            bbox={"x": 5, "y": 5, "w": 40, "h": 15}, engine="hwr", model_version="v1", config_version="v1",
            entry_status="live",
        )
        session.add(extraction)
        session.flush()

        record_provenance(session, extraction=extraction, document_id=doc.id)
        # Small edit distance (<= MAKER_CHECKER_EDIT_DISTANCE_THRESHOLD) —
        # lands as a real Correction row, not a PendingCorrection, so it
        # shows up in resolve_provenance's edit_history.
        submit_correction(session, extraction=extraction, corrected=SECRET_CORRECTED, actor="operator1", stream="routed")

        record = publish_new_version(
            session, record_group_id=None, batch_id=batch.id, actor="supervisor1", parcel_ref="masking-test-parcel",
        )
        assembly = RecordAssembly(
            record_id=record.record_group_id, extraction_ids=[extraction.id],
            strategy="single_page", rationale="P4-10b masking test fixture", actor="system",
        )
        session.add(assembly)
        session.commit()

        return {"extraction_id": extraction.id, "record_group_id": record.record_group_id}


# ---------------------------------------------------------------------------
# The sweep: every mounted route (minus the stated exclusions) × every role.
# ---------------------------------------------------------------------------

_CASES = [(method, path, role, actor) for (method, path) in SWEPT_ROUTES for role, actor in ROLE_ACTORS.items()]


@pytest.mark.parametrize(
    "method,path,role,actor", _CASES,
    ids=[f"{m}_{p}_as_{role}" for m, p, role, actor in _CASES],
)
def test_no_route_leaks_the_seeded_secret_to_any_role(client, seeded, method, path, role, actor):
    resolved_path = _resolve_path(path, seeded)
    if method == "GET":
        resp = client.get(resolved_path, headers={"X-Actor": actor})
    else:
        resp = client.request(method, resolved_path, json={}, headers={"X-Actor": actor})

    if resp.status_code == 501:
        # Routes returning 501 must NOT count as passing — skip explicitly
        # with a reason, so a stub can never satisfy this suite by
        # returning nothing (P4-10b's own requirement). Today this is
        # GET /dashboard/metrics; api/dashboard.py is out of scope for
        # this block.
        pytest.skip(f"{method} {path} is a known-unimplemented stub (501) — not counted as passing (P4-10b)")

    body_text = resp.text
    assert SECRET_VALUE not in body_text, f"{method} {resolved_path} as {role} returned the seeded raw/current value"
    assert SECRET_CORRECTED not in body_text, f"{method} {resolved_path} as {role} returned the seeded corrected value"


# ---------------------------------------------------------------------------
# Dedicated checks beyond "the secret string is absent" — a signed crop URL
# doesn't contain the secret text at all, so its *presence* (not its
# content) is what the third FR-SEC-02 place actually requires checking.
# ---------------------------------------------------------------------------


def test_records_route_masks_value_and_raw_value_and_never_issues_a_crop_url(client, seeded):
    for role, actor in ROLE_ACTORS.items():
        resp = client.get(f"/records/{seeded['record_group_id']}", headers={"X-Actor": actor})
        assert resp.status_code == 200, f"{role}: {resp.text}"
        body = resp.json()
        owner_fields = [f for f in body["fields"] if f["field_name"] == "owner_name"]
        assert owner_fields, "expected the seeded owner_name field in the response"
        for f in owner_fields:
            assert f["masked"] is True, f"{role} saw an unmasked owner_name field"
            assert f["value"] == MASK_TOKEN
            assert f["raw_value"] == MASK_TOKEN
            assert f["crop_uri"] is None, f"{role} was issued a crop_uri for a masked personal-data field"


def test_provenance_route_masks_edit_history_for_every_entitled_role(client, seeded):
    """Directly exercises the leak this block's own fix closed
    (backend.api.records.get_provenance now calls mask_edit_history_entry).
    Roles that don't hold PROVENANCE_READ (operator, administrator) get a
    403 — nothing to check there, the generic sweep above already covers
    "a 403 body can't leak"."""
    saw_at_least_one_entitled_role = False
    for role, actor in ROLE_ACTORS.items():
        resp = client.get(f"/extractions/{seeded['extraction_id']}/provenance", headers={"X-Actor": actor})
        if resp.status_code == 403:
            continue
        saw_at_least_one_entitled_role = True
        assert resp.status_code == 200, f"{role}: {resp.text}"
        body = resp.json()
        assert body["edit_history"], "expected the seeded Correction to appear in edit_history"
        for entry in body["edit_history"]:
            assert entry["masked"] is True, f"{role} saw an unmasked edit_history entry"
            assert entry["predicted"] == MASK_TOKEN
            assert entry["corrected"] == MASK_TOKEN
    assert saw_at_least_one_entitled_role, "no role reached 200 on the provenance route — test fixture/permissions drifted"


# ---------------------------------------------------------------------------
# The one route excluded from the blanket sweep gets its own, sharper pair
# of assertions: the entitled role really does see the value (that's the
# route's job), and every other role/condition is refused outright.
# ---------------------------------------------------------------------------


def test_unmasked_read_route_reveals_value_only_to_the_entitled_role_with_purpose(client, seeded):
    resp = client.post(
        f"/extractions/{seeded['extraction_id']}/unmasked-read",
        json={"record_id": seeded["record_group_id"], "version": 1, "purpose": "dispute investigation"},
        headers={"X-Actor": "auditor1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["value"] == SECRET_VALUE
    assert body["raw_value"] == SECRET_VALUE


@pytest.mark.parametrize(
    "role,actor", [(r, a) for r, a in ROLE_ACTORS.items() if r != "auditor"],
)
def test_unmasked_read_route_refuses_every_role_but_auditor(client, seeded, role, actor):
    resp = client.post(
        f"/extractions/{seeded['extraction_id']}/unmasked-read",
        json={"record_id": seeded["record_group_id"], "version": 1, "purpose": "dispute investigation"},
        headers={"X-Actor": actor},
    )
    assert resp.status_code == 403, f"{role} should not be able to reach an unmasked read"
    assert SECRET_VALUE not in resp.text


def test_unmasked_read_route_requires_a_nonempty_purpose_even_for_the_entitled_role(client, seeded):
    resp = client.post(
        f"/extractions/{seeded['extraction_id']}/unmasked-read",
        json={"record_id": seeded["record_group_id"], "version": 1, "purpose": ""},
        headers={"X-Actor": "auditor1"},
    )
    assert resp.status_code == 422
    assert SECRET_VALUE not in resp.text


# ---------------------------------------------------------------------------
# T4.b's auditor conjunction — both halves in one test, since the PRD's
# acceptance criterion is their conjunction, not each proven in isolation.
# ---------------------------------------------------------------------------


def test_auditor_conjunction_verifies_chain_while_personal_data_stays_masked(client, seeded):
    chain_resp = client.get("/chain/verify", headers={"X-Actor": "auditor1"})
    assert chain_resp.status_code == 200, chain_resp.text
    assert "structural_ok" in chain_resp.json()

    record_resp = client.get(f"/records/{seeded['record_group_id']}", headers={"X-Actor": "auditor1"})
    assert record_resp.status_code == 200
    owner_fields = [f for f in record_resp.json()["fields"] if f["field_name"] == "owner_name"]
    assert owner_fields and all(f["masked"] for f in owner_fields)
    assert SECRET_VALUE not in record_resp.text
