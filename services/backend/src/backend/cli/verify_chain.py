#!/usr/bin/env python3
"""P4-06 — the standalone `verify-chain` CLI. Deliverable's own wording:
"runs against a DB it does not trust, takes an anchored root as input,
exits non-zero with a specific finding."

"Runs against a DB it does not trust": this connects with `DATABASE_URL`
— by default the exact same credential the application itself writes
with (T4.a explicitly wants this run "with the app's own DB credentials,
to prove the app cannot repair its own chain"), and treats what it reads
back as untrusted. Trust lives only in `--anchored-root`, which MUST come
from somewhere this DB read cannot have influenced (fetched from the
anchor store directly, or typed in by an operator who already has it) —
see `landaudit.verify`'s module docstring for exactly why a root read
back out of this same DB's own `chain_root` table would defeat the point.

Exit codes: 0 (verified clean), 1 (structural or anchor mismatch found —
message printed names the shard and, when available, the bounded tamper
window), 2 (usage error / could not connect).
"""
from __future__ import annotations

import argparse
import sys

from landaudit import verify_against_anchor
from sqlalchemy.orm import Session

from backend.models.base import engine_from_env


def _fetch_from_anchor_store(key: str) -> str:
    """Read a previously-anchored root's payload back from the anchor
    store (`ANCHOR_STORE_*` env vars — the same third, independently
    credentialed namespace `backend.domain.anchoring.anchor_store` uses),
    given the `witness_reference` key an `anchor_receipt` recorded. Kept
    separate from `--anchored-root` (a bare string) because an operator
    fetching the root out-of-band (e.g. from a printed daily report, or a
    second person's independent read of the anchor store) is exactly the
    trust-establishing step this tool cannot automate away — this
    function is a convenience for the common case, not the only path.
    """
    from backend.domain.anchoring import anchor_store

    payload = anchor_store().get(key)
    # SecondStoreWitness.anchor() writes "{root}|{anchored_at}|{signature}"
    return payload.decode().split("|", 1)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify-chain",
        description="P4-06 — verify the audit hash chain against an externally-anchored root.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--anchored-root", help="the trusted root value, fetched independently of this DB")
    source.add_argument(
        "--anchor-store-key",
        help="fetch the trusted root from the anchor store at this witness_reference key "
        "(ANCHOR_STORE_* credentials, never this app's primary/secondary object store)",
    )
    parser.add_argument(
        "--structural-only", action="store_true",
        help="skip the anchor comparison; only run the per-shard structural check (weaker — see landaudit.verify)",
    )
    args = parser.parse_args(argv)

    try:
        engine = engine_from_env()
    except Exception as exc:  # noqa: BLE001 — CLI top level: any connection failure is a usage error, reported plainly
        print(f"verify-chain: could not build a DB engine from DATABASE_URL: {exc}", file=sys.stderr)
        return 2

    trusted_root = None
    if not args.structural_only:
        try:
            trusted_root = args.anchored_root or _fetch_from_anchor_store(args.anchor_store_key)
        except Exception as exc:  # noqa: BLE001 — same posture as above
            print(f"verify-chain: could not obtain the anchored root: {exc}", file=sys.stderr)
            return 2

    with Session(engine) as session:
        try:
            report = verify_against_anchor(session, trusted_root=trusted_root)
        except Exception as exc:  # noqa: BLE001 — report the DB failure, don't crash with a traceback
            print(f"verify-chain: verification failed to run: {exc}", file=sys.stderr)
            return 2

    for finding in report.structural_findings:
        status = "OK" if finding.ok else "TAMPERED"
        print(f"shard {finding.shard_id}: {status} — {finding.detail}")

    print(f"computed root: {report.computed_root}")
    if report.supplied_anchor_root is not None:
        print(f"supplied anchored root: {report.supplied_anchor_root}")
        print(f"anchor match: {'YES' if report.anchor_ok else 'NO'}")

    if report.tamper_window is not None:
        w = report.tamper_window
        print(
            f"TAMPER WINDOW (shard {w.shard_id}): last agreeing anchor "
            f"{w.last_agreeing_root_id!r} at {w.last_agreeing_rolled_at}; earliest disagreeing anchor "
            f"{w.earliest_disagreeing_root_id!r} at {w.earliest_disagreeing_rolled_at}"
        )
        print(w.detail)

    if report.ok:
        print("verify-chain: OK — chain verifies end to end.")
        return 0

    print("verify-chain: FAILED — see findings above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
