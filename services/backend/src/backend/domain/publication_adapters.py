"""P4-12/FR-PUB-04 — one `PublicationAdapter` protocol, mock
implementations for LRMS, DILRMP, and GIS (ground rule 3: interfaces at
P0, integrations at P1 — do not build a real client for any of the
three). All three satisfy the identical shared contract test
(`services/backend/tests/contract/test_publication_adapter_contract.py`).

P4-13/FR-PUB-07/FR-CFL-06 — a downstream counterparty (any adapter) can
report a defect in a record it received; that intake reuses Phase 3's
conflict register completely unchanged (`backend.domain.conflict_register
.open_conflict(origin="downstream", ...)` already exists and already
accepts `origin="downstream"` — see that module's `VALID_STATES`/`origin`
handling, untouched by this phase). `report_downstream_defect` below is
the one new function this phase adds: a thin translation from "an
adapter's mock counterparty reported a problem" to the conflict register's
existing call shape, tagged to the `downstream` correction stream via
`Correction.stream` at the point a fix is actually submitted (P3-07's
maker-checker path already threads `stream` through
`submit_correction`) — nothing about resolution-tagging needed new code,
only this intake seam.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.domain.conflict_register import open_conflict
from backend.models.entities import Conflict


@dataclass(frozen=True)
class AdapterPublishResult:
    accepted: bool
    external_reference: str | None
    detail: str


class PublicationAdapter(abc.ABC):
    """The one interface every downstream-publication integration
    implements. `publish` hands a record's public (already-masked, if the
    adapter is a public-facing counterparty — callers decide what view to
    pass) shape to the counterparty; `report_defect` is how *that*
    counterparty tells this system something is wrong with what it
    received (P4-13's intake)."""

    name: str

    @abc.abstractmethod
    def publish(self, record: dict) -> AdapterPublishResult: ...

    @abc.abstractmethod
    def report_defect(self, *, record_id: str, rule: str, evidence: dict) -> None: ...


class _MockAdapter(PublicationAdapter):
    """Shared mock behavior for all three counterparties — ground rule 3:
    "do not build a real LRMS client or hard-code a single witness"
    applies equally here; each subclass differs only in `name`, matching
    how the build prompt frames LRMS/DILRMP/GIS as the same shape of
    integration with different counterparties, not three different
    protocols."""

    def __init__(self, session: Session):
        self._session = session
        self._published: list[dict] = []  # in-memory — this mock has no real counterparty store

    def publish(self, record: dict) -> AdapterPublishResult:
        self._published.append(record)
        return AdapterPublishResult(
            accepted=True, external_reference=f"{self.name}-mock-ref-{len(self._published)}",
            detail=f"accepted by {self.name} mock",
        )

    def report_defect(self, *, record_id: str, rule: str, evidence: dict) -> Conflict:
        """P4-13 — routes straight into the (unchanged) conflict register,
        `origin='downstream'`. Returns the opened `Conflict` so a test can
        assert it landed within one processing cycle (T4.f) — this call IS
        the processing cycle; there is no queue hop in between at P0."""
        return open_conflict(
            self._session, records=[record_id], rule=rule,
            evidence={**evidence, "reported_by": self.name}, origin="downstream",
        )


class LRMSAdapter(_MockAdapter):
    name = "lrms"


class DILRMPAdapter(_MockAdapter):
    name = "dilrmp"


class GISAdapter(_MockAdapter):
    name = "gis"


ADAPTERS: dict[str, type[_MockAdapter]] = {"lrms": LRMSAdapter, "dilrmp": DILRMPAdapter, "gis": GISAdapter}
