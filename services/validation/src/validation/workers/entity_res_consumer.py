"""Consumes ENTITY_RES_QUEUE (canonical Extraction[] + ParcelGeometry).
TODO: FR-ENT-01-05."""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    # TODO: perceptual-hash rescan detection (FR-ENT-01) BEFORE extraction is
    # spent on a duplicate scan, then multi-pass parcel de-dup (FR-ENT-02),
    # then person matching (FR-ENT-03, P1). Never auto-merge (FR-ENT-04).
    raise NotImplementedError("TODO: FR-ENT-01..05 not implemented")
