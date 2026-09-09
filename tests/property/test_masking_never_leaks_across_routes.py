"""ADR-007's property: 'no route can leak a masked field' is checked once,
against the policy function and the response models, not once per
endpoint. Extends libs/masking's own unit tests (which check the policy
function in isolation) up to the actual response model
services/backend's API layer returns.
"""
from backend.api.serializers import MaskedExtractionView, ReviewTaskPublicView
from landmasking import PERSONAL_DATA_FIELD_CLASSES, MASK_TOKEN, apply


def test_masked_extraction_view_has_no_field_that_bypasses_the_policy():
    """Every field on the response model that could carry a value or a
    crop reference is one `mask_extraction_for_role` actually populates
    from `landmasking.apply`'s output — there is no additional field on
    this model an endpoint could fill in directly, bypassing the policy.
    """
    assert set(MaskedExtractionView.model_fields) == {
        "id", "page_id", "field_name", "value", "raw_value", "crop_uri", "entry_status", "masked",
    }


def test_review_task_public_view_has_no_source_stream_shaped_field():
    """FR-REV-11's structural guarantee, restated as a property over the
    model's field names rather than one example row — no field on this
    model is named (or could be mistaken for) `source_stream`."""
    forbidden_substrings = ("source_stream", "stream")
    for field_name in ReviewTaskPublicView.model_fields:
        assert not any(s in field_name for s in forbidden_substrings), field_name


def test_every_personal_data_field_class_masks_to_the_same_token_regardless_of_content():
    """A property over the whole PERSONAL_DATA_FIELD_CLASSES set, not one
    example: masking never depends on what the value happens to look
    like (no accidental passthrough for e.g. an empty string, a name that
    looks like a survey number, etc.)."""
    sample_values = ["Ram Prasad", "", "145/2A", "राम प्रसाद ठाकुर", "0"]
    for field_class in PERSONAL_DATA_FIELD_CLASSES:
        for value in sample_values:
            result = apply(value, value, "uri", field_class=field_class, role="operator")
            assert result.value == MASK_TOKEN
            assert result.raw_value == MASK_TOKEN
            assert result.crop_uri is None
