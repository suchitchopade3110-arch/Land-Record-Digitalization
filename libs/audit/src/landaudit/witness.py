"""FR-PUB-08 — a witness outside this application's administrative
control. Renamed from `anchor.AnchorWitness` this phase to match the build
prompt's naming (`Witness`); `anchor.py` re-exports the old names as a
thin compatibility shim, nothing else in this repo imported them directly
before this phase (checked: only `landaudit/__init__.py` did).

Counterparty is still not decided (PRD §11 Q8 — see
docs/open-questions.md); this module defines the interface the decision
plugs into, plus two P0 stand-ins:

- `SecondStoreWitness` — a second store under different credentials
  (P4-05's "real mechanism, mock counterparty": the *separation* is real,
  the *counterparty being external to this application's own
  infrastructure* is not yet, since both stores run in this repo's own
  `docker-compose.yml`).
- `TimestampAuthorityWitness` — a stub for an RFC 3161-shaped timestamping
  authority. Deliberately does not implement a real TSA protocol or name
  a real TSA endpoint (Q8 is unanswered) — it returns a
  locally-fabricated "timestamp token" shaped like a receipt, clearly
  marked as such, so callers can exercise the `Witness` interface's
  two-implementations requirement (ground rule 3) without this repo
  claiming an integration that does not exist.

Every real implementation must be reachable under credentials the
application's own database-write credentials do not also grant — that is
what "outside this application's administrative control" means
operationally, and it is exactly what T4.a exercises (tamper via the
app's own DB credentials; verification against the anchor fails).
"""
from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from landaudit.kms import SignOnlyKeyHandle


@dataclass(frozen=True)
class WitnessReceipt:
    """In-memory return value of `Witness.anchor()`. Persisted verbatim
    into the `anchor_receipt` table by `rollup.perform_roll` — this
    dataclass is not itself the source of truth once a roll has run; the
    DB row is (see `landaudit.models.AnchorReceipt`)."""

    root: str
    anchored_at: str
    witness: str
    witness_reference: str  # e.g. a second store's object key, or (once Q8 resolves) a TSA token
    signature: str | None = None
    kms_key_id: str | None = None


class Witness(abc.ABC):
    """A witness outside this application's administrative control. A
    real implementation must not be reachable with the same credentials
    that can write to this application's own Postgres — otherwise it
    anchors nothing against the threat FR-PUB-08 names (an insider with
    this application's DB write access)."""

    @abc.abstractmethod
    def anchor(self, root: str, *, signer: SignOnlyKeyHandle | None = None) -> WitnessReceipt: ...


class SecondStoreWitness(Witness):
    """P0 stand-in: writes a signed root to a second store under
    different credentials than the primary object store or the primary
    database (`infra/docker-compose.yml`'s `anchor_store` service — see
    PHASE4.md's "P0 form of the second store" section for exactly which
    credential the application container does and does not hold). The
    *mechanism* (root computed once, signed by a handle the app cannot
    export, written somewhere the primary write path doesn't touch,
    receipt returned and itself auditable) is real; the *off-infrastructure
    placement* that would make it a genuine external witness — a
    counterparty this organization does not itself administer — is not
    yet, since PRD §11 Q8 is unanswered. Do not present this class as
    satisfying FR-PUB-08 in a pilot or production context without Q8
    being resolved first.
    """

    def __init__(self, store):
        # `store` is any `landstorage.ObjectStorePort`, but MUST be one
        # constructed against the anchor store's own write-only append
        # credential (never `landstorage.get_store()`/`get_secondary_store()`,
        # which are the *application's* primary/secondary stores) — see
        # `backend.domain.anchoring.anchor_store()`.
        self._store = store

    def anchor(self, root: str, *, signer: SignOnlyKeyHandle | None = None) -> WitnessReceipt:
        anchored_at = datetime.now(timezone.utc).isoformat()
        signature = signer.sign(root.encode()) if signer is not None else None
        payload = f"{root}|{anchored_at}|{signature or ''}".encode()
        result = self._store.put(payload)
        return WitnessReceipt(
            root=root,
            anchored_at=anchored_at,
            witness="second_store",
            witness_reference=result.key,
            signature=signature,
            kms_key_id=signer.key_id if signer is not None else None,
        )


class TimestampAuthorityWitness(Witness):
    """Stub for an RFC 3161-shaped external timestamping authority.
    Deliberately not a real TSA client — PRD §11 Q8 (anchoring
    counterparty) is unanswered, so no real TSA endpoint is named or
    called. Exists so `Witness` genuinely has two implementations at P0
    (ground rule 3), and so the shared contract test
    (`tests/contract/test_witness_contract.py`) proves both satisfy the
    same interface — swap-in-ready for whichever TSA (or other
    counterparty) Q8 eventually names, with zero caller-side changes.
    """

    def anchor(self, root: str, *, signer: SignOnlyKeyHandle | None = None) -> WitnessReceipt:
        anchored_at = datetime.now(timezone.utc).isoformat()
        signature = signer.sign(root.encode()) if signer is not None else None
        # A "token" shaped like what a real TSA would return (an opaque
        # reference to a record it holds) but fabricated locally and
        # labeled as such — never mistaken for a genuine RFC 3161 token.
        fabricated_token = f"stub-tsa-token:{uuid.uuid4()}"
        return WitnessReceipt(
            root=root,
            anchored_at=anchored_at,
            witness="timestamp_authority_stub",
            witness_reference=fabricated_token,
            signature=signature,
            kms_key_id=signer.key_id if signer is not None else None,
        )
