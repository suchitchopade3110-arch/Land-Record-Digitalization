"""Demo increment, "scripted moment 2 — the refusal." Pure function, no DB."""
import pytest

from backend.domain.unit_table import NoUnitTableForDistrict, unit_table_for_district


def test_a_known_district_returns_a_unit_table():
    table = unit_table_for_district("sitapur")
    assert table["district"] == "sitapur"


def test_an_unconfigured_district_blocks_rather_than_guesses():
    with pytest.raises(NoUnitTableForDistrict) as exc_info:
        unit_table_for_district("some district with no configured table")
    assert exc_info.value.reason_code == "no_unit_table_for_district"
    assert exc_info.value.district == "some district with no configured table"


def test_district_matching_is_case_and_whitespace_insensitive():
    assert unit_table_for_district(" Sitapur ")["district"] == " Sitapur "
