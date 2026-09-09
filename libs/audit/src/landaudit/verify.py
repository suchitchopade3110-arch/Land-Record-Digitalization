"""P4-06/FR-PUB-03/FR-PUB-08 — end-to-end chain verification, the logic
behind the standalone `verify-chain` CLI (`services/backend/src/backend/
cli/verify_chain.py`). Split from the CLI itself so the same logic is
directly unit-testable without a subprocess.

Two independent checks, run together:

1. **Structural** (`structural_findings`): does each shard's stored chain
   recompute consistently from genesis (`chain.verify_shard`)? This alone
   is NOT sufficient — an attacker who tampers with one entry and then
   recomputes every *subsequent* entry's `prev_hash`/`hash` forward from
   that point produces a shard that is perfectly self-consistent again.
   T4.a's whole point is this exact attack.

2. **Against an anchored root** (`verify_against_anchor`): the caller
   supplies a root it trusts came from *outside* this database — fetched
   from the second store / TSA independently of this DB read (never a
   root read back out of this same DB's own `chain_root`/`anchor_receipt`
   tables, which an attacker with this DB's write credentials could have
   tampered exactly as easily as `audit_entry` itself). Recomputes the
   current Merkle root and compares. A trusted-root mismatch is the
   signal a forward-recomputed shard cannot hide from: the attacker can
   make today's shard internally self-consistent, but cannot make it
   hash to the value an already-anchored, externally-held root committed
   to *before* the tamper happened.

To *bound* the tamper window once a mismatch is found, this module also
walks the DB's own `chain_root` history (§3, `bound_tamper_window`) —
explicitly caveated as **not independently trusted**: it is a best-effort
localization using the same (possibly-tampered) database, useful because
an attacker who only tampered `audit_entry` (not also every `chain_root`
row) still leaves a detectable trail there, but it does not carry the
same guarantee as the externally-anchored root comparison above.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from landaudit.chain import NUM_SHARDS, ChainTamperedError, verify_shard
from landaudit.merkle import compute_root, current_shard_heads
from landaudit.models import AuditEntry, ChainRoot


@dataclass(frozen=True)
class ShardFinding:
    shard_id: int
    ok: bool
    detail: str


@dataclass(frozen=True)
class TamperWindow:
    """Bounds on when a discrepancy this database's own roll history can
    account for was introduced. Both timestamps come from `chain_root`
    rows in THIS database — see this module's docstring's trust caveat."""

    shard_id: int
    last_agreeing_root_id: str | None
    last_agreeing_rolled_at: str | None
    earliest_disagreeing_root_id: str
    earliest_disagreeing_rolled_at: str
    detail: str


@dataclass(frozen=True)
class VerificationReport:
    structural_ok: bool
    structural_findings: list[ShardFinding]
    anchor_ok: bool | None  # None if no anchored root was supplied to compare against
    computed_root: str
    supplied_anchor_root: str | None
    tamper_window: TamperWindow | None  # set only when a mismatch was found and could be localized

    @property
    def ok(self) -> bool:
        return self.structural_ok and (self.anchor_ok is not False)


def structural_findings(session: Session) -> list[ShardFinding]:
    """Check #1 — see module docstring. Runs every shard even after the
    first failure (a real operator wants to know about every affected
    shard in one pass, not stop at the first)."""
    findings = []
    for shard_id in range(NUM_SHARDS):
        try:
            verify_shard(session, shard_id, raise_on_mismatch=True)
            findings.append(ShardFinding(shard_id=shard_id, ok=True, detail="chain recomputes consistently"))
        except ChainTamperedError as exc:
            findings.append(ShardFinding(shard_id=shard_id, ok=False, detail=str(exc)))
    return findings


def bound_tamper_window(session: Session, *, shard_id: int) -> TamperWindow | None:
    """Best-effort localization using this DB's own `chain_root` history
    — see the module docstring's trust caveat. Walks rolls chronologically
    and finds the earliest one whose recorded head hash for `shard_id` no
    longer exists among today's entries in that shard: an untampered head
    hash, once recorded, can never stop existing (this chain never deletes
    or updates rows — see the `audit_entry` append-only trigger); if it's
    gone, the entries at or after that point were rewritten after that
    roll ran."""
    rolls = session.execute(select(ChainRoot).order_by(ChainRoot.rolled_at.asc())).scalars().all()
    last_agreeing: ChainRoot | None = None
    for roll in rolls:
        head = next((h for h in roll.shard_heads if h["shard_id"] == shard_id), None)
        if head is None or head["head_hash"] is None:
            continue
        still_present = session.execute(
            select(AuditEntry.id).where(AuditEntry.shard_id == shard_id, AuditEntry.hash == head["head_hash"])
        ).first()
        if still_present is None:
            return TamperWindow(
                shard_id=shard_id,
                last_agreeing_root_id=last_agreeing.id if last_agreeing else None,
                last_agreeing_rolled_at=last_agreeing.rolled_at.isoformat() if last_agreeing else None,
                earliest_disagreeing_root_id=roll.id,
                earliest_disagreeing_rolled_at=roll.rolled_at.isoformat(),
                detail=(
                    f"shard {shard_id}'s head hash as recorded in chain_root {roll.id} "
                    f"(rolled at {roll.rolled_at.isoformat()}) no longer exists in the shard's current "
                    "chain — the tamper (and any local forward-recompute covering it) happened no earlier "
                    f"than {last_agreeing.rolled_at.isoformat() if last_agreeing else 'the start of the chain'} "
                    f"and no later than {roll.rolled_at.isoformat()}"
                ),
            )
        last_agreeing = roll
    return None


def verify_against_anchor(session: Session, *, trusted_root: str | None) -> VerificationReport:
    """The full check. `trusted_root` must come from outside this
    database (the second store, or an operator-supplied value fetched
    independently) — passing a root read out of this same DB's own
    `chain_root` table defeats the entire point; the CLI enforces this by
    only ever accepting the root as a command-line argument, never as a
    query against this DB."""
    findings = structural_findings(session)
    structural_ok = all(f.ok for f in findings)

    heads = current_shard_heads(session)
    computed_root = compute_root(heads)

    anchor_ok: bool | None = None
    tamper_window: TamperWindow | None = None
    if trusted_root is not None:
        anchor_ok = computed_root == trusted_root
        if not anchor_ok:
            # Localize against every shard; report the earliest-bounded one
            # (the one whose disagreeing roll happened soonest) first.
            windows = [w for sid in range(NUM_SHARDS) if (w := bound_tamper_window(session, shard_id=sid))]
            windows.sort(key=lambda w: w.earliest_disagreeing_rolled_at)
            tamper_window = windows[0] if windows else None

    return VerificationReport(
        structural_ok=structural_ok,
        structural_findings=findings,
        anchor_ok=anchor_ok,
        computed_root=computed_root,
        supplied_anchor_root=trusted_root,
        tamper_window=tamper_window,
    )
