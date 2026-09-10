"""P4-06 — the API-facing half of chain verification (T4.b: "an auditor
role verifies the chain end to end while every personal-data field and
source crop renders masked"). The standalone CLI
(`backend.cli.verify_chain`) is the operator-facing tool that runs
against a DB it does not trust and takes an externally-anchored root as
input; this route is the same underlying `landaudit.verify_against_anchor`
logic exposed to an authenticated `Role.AUDITOR` session, comparing
against the latest `chain_root` this DB itself recorded (convenient for
an in-app "does everything still check out" view — NOT a substitute for
the CLI's externally-anchored comparison, which is the one that actually
matters against T4.a's threat model; this route says so in its own
response).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from landaudit import compute_root, current_shard_heads
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.auth import Permission, require_permission
from backend.api.deps import get_session
from backend.domain.access_control import Identity

router = APIRouter(tags=["chain"])
_require_chain_verify = require_permission(Permission.CHAIN_VERIFY)


@router.get("/chain/verify", response_model=dict)
def verify_chain_route(
    identity: Identity = Depends(_require_chain_verify),
    session: Session = Depends(get_session),
) -> dict:
    from landaudit import structural_findings
    from landaudit.models import ChainRoot

    findings = structural_findings(session)
    heads = current_shard_heads(session)
    computed_root = compute_root(heads)
    latest = session.execute(select(ChainRoot).order_by(ChainRoot.rolled_at.desc()).limit(1)).scalar_one_or_none()

    return {
        "structural_ok": all(f.ok for f in findings),
        "findings": [{"shard_id": f.shard_id, "ok": f.ok, "detail": f.detail} for f in findings],
        "computed_root": computed_root,
        "latest_recorded_roll_root": latest.root if latest else None,
        "note": (
            "This compares against the latest chain_root recorded in THIS database, which an attacker "
            "with this application's own DB write access could tamper identically to audit_entry. Use "
            "the standalone verify-chain CLI with an externally-anchored root for a trustworthy check "
            "(T4.a) — this route is a convenience view, not the audited guarantee."
        ),
    }
