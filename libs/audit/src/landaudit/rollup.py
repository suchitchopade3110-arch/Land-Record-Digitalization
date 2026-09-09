"""P4-04/P4-05 — the periodic job: roll the global root, anchor it via a
`Witness`, persist both. `backend.workers` schedules `perform_roll` on the
configured interval (`backend.domain.publication_policy.ROOT_ROLL_INTERVAL`,
default hourly) and cadence (`ANCHOR_CADENCE`, defaults to every roll —
see PHASE4.md's "root roll interval, anchor cadence" section for exactly
what "cadence" means when it's coarser than "every roll").
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from landaudit.kms import SignOnlyKeyHandle
from landaudit.merkle import roll_global_root
from landaudit.models import AnchorReceipt as AnchorReceiptRow
from landaudit.models import ChainRoot
from landaudit.witness import Witness


def perform_roll(
    session: Session,
    *,
    witness: Witness,
    signer: SignOnlyKeyHandle | None = None,
    config_version: str | None = None,
    anchor: bool = True,
) -> ChainRoot:
    """Compute this roll's root, persist the `chain_root` row, and (when
    `anchor=True` — see the cadence note above, a root can be rolled more
    often than it is anchored) call the witness and persist the
    `anchor_receipt` row. Returns the persisted `ChainRoot`; the caller
    commits (same convention as `chain.append` — this function flushes,
    never commits, so a caller can wrap it in a larger transaction if it
    wants roll+anchor atomic with something else)."""
    root, heads = roll_global_root(session)
    chain_root = ChainRoot(
        root=root,
        shard_heads=[{"shard_id": h.shard_id, "head_hash": h.head_hash} for h in heads],
        config_version=config_version,
    )
    session.add(chain_root)
    session.flush()

    if anchor:
        receipt = witness.anchor(root, signer=signer)
        session.add(
            AnchorReceiptRow(
                chain_root_id=chain_root.id,
                root=receipt.root,
                anchored_at=receipt.anchored_at,
                witness=receipt.witness,
                witness_reference=receipt.witness_reference,
                signature=receipt.signature,
                kms_key_id=receipt.kms_key_id,
            )
        )
        session.flush()

    return chain_root
