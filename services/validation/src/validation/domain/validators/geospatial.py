"""Geospatial validator — recorded area vs. polygon area, within tolerance.
Distinguishes AREA_DISAGREES from UNIT_TABLE_UNVERIFIED. TODO: FR-VAL-05 (P1).
Needs Shree's polygon area (services/extraction)."""


def validate(extraction: dict, parcel_geometry: dict) -> dict:
    raise NotImplementedError("TODO: FR-VAL-05 (P1) not implemented")
