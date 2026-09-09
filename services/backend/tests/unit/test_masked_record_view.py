"""P4-10/T4.c-shaped, at the serializer-unit level (no DB needed —
`mask_extraction_for_role` and `build_masked_record_view` are pure
functions of a dict + a role once given a real `ObjectStorePort`, and
`local_fs` needs no network). Covers the "three places" rule (ADR-007)
and "the crop is masked by refusing to issue the signed URL, not by
blurring after the fact" (P4-10) directly.
"""
import tempfile

import pytest
from backend.api.serializers import build_masked_record_view, mask_extraction_for_role


@pytest.fixture(autouse=True)
def _local_object_store(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("OBJECT_STORE_DRIVER", "local_fs")
    monkeypatch.setenv("OBJECT_STORE_ROOT", tmp)
    # Seed one object at a known key so sign_get has something to point at.
    from landstorage import get_store

    get_store.cache_clear()
    store = get_store()
    store.put(b"fake page image bytes")
    yield
    get_store.cache_clear()


def _personal_data_extraction(page_storage_uri: str | None = "sha256/ab/cd/" + "0" * 64) -> dict:
    return {
        "id": "ex-1",
        "page_id": "page-1",
        "field_name": "owner_name",
        "canonical_value": "Ram Prasad",
        "raw_value": "Ram Prasad (raw)",
        "bbox": {"x": 1, "y": 2, "w": 3, "h": 4},
        "entry_status": "live",
        "page_storage_uri": page_storage_uri,
    }


def _non_personal_extraction() -> dict:
    return {
        "id": "ex-2",
        "page_id": "page-1",
        "field_name": "survey_number",
        "canonical_value": "123/4",
        "raw_value": "123/4",
        "bbox": {"x": 5, "y": 6, "w": 7, "h": 8},
        "entry_status": "live",
        "page_storage_uri": None,
    }


def test_operator_role_sees_personal_data_masked_in_all_three_places():
    view = mask_extraction_for_role(_personal_data_extraction(), role="operator")
    assert view.masked is True
    assert view.value != "Ram Prasad"
    assert view.raw_value != "Ram Prasad (raw)"
    assert view.crop_uri is None  # refused entirely, not a blurred placeholder URL


def test_auditor_role_also_sees_personal_data_masked_never_gets_a_free_pass():
    """The ordinary `auditor` role — as opposed to the distinguished
    `auditor_unmasked_read` sentinel P4-08's separate operation uses —
    must see exactly the same masking as any other role on the normal
    read path (T4.b's whole point: an auditor verifies the chain while
    everything renders masked)."""
    view = mask_extraction_for_role(_personal_data_extraction(), role="auditor")
    assert view.masked is True
    assert view.crop_uri is None


def test_the_distinguished_unmasked_read_role_sees_everything_unmasked():
    view = mask_extraction_for_role(_personal_data_extraction(), role="auditor_unmasked_read")
    assert view.masked is False
    assert view.value == "Ram Prasad"
    assert view.raw_value == "Ram Prasad (raw)"
    assert view.crop_uri is not None


def test_non_personal_data_field_is_never_masked_for_any_role():
    for role in ("operator", "verifier", "supervisor", "auditor", "administrator"):
        view = mask_extraction_for_role(_non_personal_extraction(), role=role)
        assert view.masked is False
        assert view.value == "123/4"


def test_masked_crop_url_is_never_generated_not_even_transiently():
    """The stronger claim P4-10 makes: a masked field's crop URL isn't
    computed-then-discarded — `mask_extraction_for_role` never calls
    `sign_get` at all when the field will be masked. Verified here by
    breaking `sign_get` and confirming a masked call still succeeds
    (i.e. never reached it), while an unmasked call would have raised."""
    from landstorage import get_store

    store = get_store()
    original_sign_get = store.sign_get

    def _boom(*a, **kw):
        raise AssertionError("sign_get should never be called for a masked field")

    store.sign_get = _boom
    try:
        view = mask_extraction_for_role(_personal_data_extraction(), role="operator")
        assert view.crop_uri is None
    finally:
        store.sign_get = original_sign_get


def test_masked_record_view_wraps_every_field_through_the_same_gate():
    view = build_masked_record_view(
        "rec-group-1", 1, "published",
        [_personal_data_extraction(), _non_personal_extraction()],
        role="operator",
    )
    assert view.record_id == "rec-group-1"
    assert len(view.fields) == 2
    masked_field = next(f for f in view.fields if f.field_name == "owner_name")
    plain_field = next(f for f in view.fields if f.field_name == "survey_number")
    assert masked_field.masked is True
    assert plain_field.masked is False


def test_missing_page_storage_uri_yields_no_crop_but_does_not_crash():
    view = mask_extraction_for_role(_non_personal_extraction(), role="operator")
    assert view.crop_uri is None
