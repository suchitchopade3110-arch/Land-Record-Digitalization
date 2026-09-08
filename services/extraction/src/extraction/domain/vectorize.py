"""Vectorize parcel boundaries into closed polygons, bind survey-number
labels, compute polygon area. TODO: FR-MAP-02/03/04."""


def vectorize_parcels(map_page: dict) -> list[dict]:
    raise NotImplementedError("TODO: FR-MAP-02 not implemented")


def bind_survey_labels(polygons: list[dict], labels: list[dict]) -> list[dict]:
    raise NotImplementedError("TODO: FR-MAP-03 not implemented")


def compute_area(polygon: dict) -> str:
    """Returns exact decimal string — never floating point (API-Contracts §1)."""
    raise NotImplementedError("TODO: FR-MAP-04 not implemented")
