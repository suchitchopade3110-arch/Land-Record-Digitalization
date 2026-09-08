"""Consumes TRIAGE_QUEUE (raw Page) and runs legibility/script/doc-type/
page-role classification + writer clustering + novelty pre-score.
TODO: FR-TRI-01/02/03/04/10/11, FR-CNF-14 (pre-score)."""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    # TODO: FR-TRI-01 legibility -> below threshold produces a RescanTask
    # trigger for Suchit's queue (this service decides the threshold, Suchit
    # owns the queue + reason code display).
    raise NotImplementedError("TODO: FR-TRI-01..04/10/11 not implemented")
