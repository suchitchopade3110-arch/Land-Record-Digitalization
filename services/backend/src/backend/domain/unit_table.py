"""Demo increment, "scripted moment 2 — the refusal": a district with no
configured unit table must block rather than guess. The real lookup lands
when Phase 5's config service (M13) ships (FR-CFG-01/02,
`backend.config._fetch_from_db`, currently `NotImplementedError`) — until
then this is a fixture (`_DISTRICTS_WITH_UNIT_TABLES`) wired against the
identical "blocked, not guessed" contract, so the eventual swap is a data
source change, not a caller change (Rule 0's stub-worker contract).
"""
from __future__ import annotations


class NoUnitTableForDistrict(Exception):
    """A first-class blocked state, not a bare exception — carries its own
    machine-readable reason code and the district name, so a caller (the
    ingest endpoint) can render the scripted refusal without string-
    matching an error message."""

    def __init__(self, district: str):
        self.reason_code = "no_unit_table_for_district"
        self.district = district
        super().__init__(f"no unit table for district {district!r}")


# TODO: FR-CFG-01/02 — replace with a real ConfigVersion lookup (scope=
# "district", key="unit_table") once the config service actually serves
# versions. Fixture set for the demo increment.
_DISTRICTS_WITH_UNIT_TABLES = frozenset({"sitapur", "lucknow", "kanpur nagar"})


def unit_table_for_district(district: str) -> dict:
    if district.strip().lower() not in _DISTRICTS_WITH_UNIT_TABLES:
        raise NoUnitTableForDistrict(district)
    # TODO: FR-CFG-01 — the real per-district unit-conversion table, once
    # the config service exists to serve it.
    return {"district": district, "bigha_to_sqm": 1008.0}
