"""Text lane pipeline: layout analysis -> printed OCR / HWR -> top-k ->
gazetteer re-rank (P1) -> joint decoding (P1) -> strikethrough detection
(P0) -> attestation detection (P1). See Architecture §10.

TODO: FR-OCR-01/02/03/04/05/06/07, FR-EXT-01/02/05/06/08.
"""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    # TODO: FR-EXT-06 (P0) — geometric strikethrough/cancellation detection.
    # No labels needed: a stroke crossing a bounding box is enough. This is
    # one of the five scripted demo moments — get it reviewable from day one.
    # entry_status MUST default to "unknown", never "live" (FR-EXT-06/07).
    raise NotImplementedError("TODO: text lane not implemented")
