"""FR-SEC-02 — proves the "one code path, three copies masked together"
property, not just that individual calls return plausible-looking output."""
import pytest

from landmasking import PERSONAL_DATA_FIELD_CLASSES, apply


@pytest.mark.parametrize("field_class", sorted(PERSONAL_DATA_FIELD_CLASSES))
@pytest.mark.parametrize("role", ["operator", "verifier", "supervisor", "auditor", "administrator"])
def test_personal_data_is_masked_in_all_three_places_for_every_ordinary_role(field_class, role):
    result = apply(
        value="Ram Prasad",
        raw_value="राम प्रसाद",
        crop_uri="s3://crops/abc123.png",
        field_class=field_class,
        role=role,
    )

    assert result.masked is True
    assert result.value != "Ram Prasad"
    assert result.raw_value != "राम प्रसाद"
    # The specific bug FR-SEC-02 calls out: masking only the current value
    # and leaving the other two unmasked copies reachable.
    assert result.crop_uri is None


def test_non_personal_field_is_never_masked():
    result = apply(
        value="145/2A",
        raw_value="145/2A",
        crop_uri="s3://crops/survey-no.png",
        field_class="survey_number",
        role="operator",
    )

    assert result.masked is False
    assert result.value == "145/2A"
    assert result.crop_uri == "s3://crops/survey-no.png"


def test_auditor_unmasked_read_is_the_one_role_that_sees_real_values():
    result = apply(
        value="Ram Prasad",
        raw_value="राम प्रसाद",
        crop_uri="s3://crops/abc123.png",
        field_class="owner_name",
        role="auditor_unmasked_read",
    )

    assert result.masked is False
    assert result.value == "Ram Prasad"
    assert result.crop_uri == "s3://crops/abc123.png"


def test_masking_never_produces_two_different_masked_states_for_the_same_call():
    """A property, not an example: for every (field_class, role) pair, the
    three fields are masked identically — never a mix."""
    for field_class in PERSONAL_DATA_FIELD_CLASSES:
        for role in ["operator", "verifier", "supervisor", "auditor", "administrator", "auditor_unmasked_read"]:
            result = apply("v", "rv", "uri", field_class=field_class, role=role)
            value_is_masked = result.value != "v"
            raw_is_masked = result.raw_value != "rv"
            crop_is_masked = result.crop_uri != "uri"
            assert value_is_masked == raw_is_masked == crop_is_masked == result.masked
