"""Legacy-digital reconciliation vs. the state's existing digital record,
reason-coded disagreement. TODO: FR-VAL-08 (P1) — gated on PRD §11 Q3/Q9
data access; ships as a no-op until resolved."""


def validate(extraction: dict, legacy_record_ref: dict | None) -> dict:
    if legacy_record_ref is None:
        # TODO: FR-VAL-08 blocked on PRD §11 Q3/Q9 — no-op until data access lands.
        return {"verdict": "not_applicable", "reason_code": "LEGACY_ACCESS_UNRESOLVED"}
    raise NotImplementedError("TODO: FR-VAL-08 (P1) not implemented")
