"""Map lane pipeline: georeference -> vectorize -> bind survey labels ->
compute area -> (P1) conservation / conflation / unit-table estimation ->
ULPIN eligibility gate. See Architecture §11.

TODO: FR-MAP-01/02/03/04/09, FR-MAP-05 (P1), FR-MAP-10/11/12 (P1).
"""
from observability import traced_consumer


@traced_consumer
def handle(message: dict) -> None:
    raise NotImplementedError("TODO: map lane not implemented")
