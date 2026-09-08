"""Multi-pass parcel de-dup: union of (a) village+survey#, (b) village+khata#,
(c) phonetic owner-name+village, (d) geometry centroid proximity. TODO: FR-ENT-02.
Report recall against seeded duplicates, not just precision (FR-ENT-05)."""


def find_duplicates(record: dict, candidates: list[dict]) -> list[dict]:
    raise NotImplementedError("TODO: FR-ENT-02 not implemented")
