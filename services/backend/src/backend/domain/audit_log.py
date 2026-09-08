"""Hash-chained, append-only audit log. TODO: FR-PUB-03/08/09.

Stores actor/action/field-identity/reason-code and VALUE HASHES, never the
value itself (FR-SEC-08). Unmasked read is a separate, separately-logged
operation. Sharded (FR-PUB-09), rolled into a global root, anchored to an
external witness outside the application's administrative control
(FR-PUB-08 — needs a counterparty decision from PRD §11 Q8).
"""


def append(actor: str, action: str, field_identity: str, reason_code: str, value_hash: str) -> None:
    raise NotImplementedError("TODO: FR-PUB-03 — hash-chained append-only write")


def anchor_root() -> None:
    raise NotImplementedError("TODO: FR-PUB-08 — blocked on PRD §11 Q8 counterparty decision")
