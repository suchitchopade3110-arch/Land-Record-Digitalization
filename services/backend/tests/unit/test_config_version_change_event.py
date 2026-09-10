"""P5-04's emission-side helper — the shape of the invalidation payload,
independent of any DB/queue (the outbox-write half is exercised together
with a real transaction once P5-02's write endpoint exists to call it
from; see `backend.domain.config_versions.publish_version_change_event`'s
own docstring for why that call site doesn't exist yet)."""
from backend.domain.config_versions import build_version_change_event
from backend.models.entities import ConfigVersion


def test_build_version_change_event_shape():
    row = ConfigVersion(
        id="cv-123",
        scope="district",
        key="unit_table.sitapur",
        value={"bigha_to_sqm": 1008.0},
        author="alice",
        approver="bob",
    )
    event = build_version_change_event(row)
    assert event == {"scope": "district", "key": "unit_table.sitapur", "config_version": "cv-123"}
