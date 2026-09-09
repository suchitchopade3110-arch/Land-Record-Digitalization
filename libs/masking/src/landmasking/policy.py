"""ADR-007 — the one function allowed to decide whether a personal-data
field is shown or masked. FR-SEC-02: an owner-name field's raw string is
personal data and so is the crop it points to; masking only the current
value leaves two unmasked copies. `apply()` takes all three together and
returns all three consistently masked or all three unmasked — there is no
calling convention that operates on just one of them.
"""
from __future__ import annotations

from dataclasses import dataclass

MASK_TOKEN = "•••"

# FR-SEC-01: five roles, least privilege, no implicit escalation. Three
# hard-coded at P0 (operator, verifier, administrator — see
# services/backend/src/backend/api/auth.py); supervisor and auditor are
# named here because the masking policy has an opinion about all five
# regardless of which are wired into auth yet.
ROLES_THAT_SEE_PERSONAL_DATA_UNMASKED = frozenset({"auditor_unmasked_read"})

# Which `Extraction.field_name` values carry personal data under DPDP,
# 2023 (PRD §M15 preamble: owner names, parentage, addresses). This is a
# starter set for P0's single pilot document type's schema
# (FR-EXT-01) — extend it, never bypass masking for a field not yet
# listed here by routing around `apply()`.
PERSONAL_DATA_FIELD_CLASSES = frozenset({"owner_name", "relationship", "address"})


@dataclass(frozen=True)
class MaskedTriple:
    value: str | None
    raw_value: str | None
    crop_uri: str | None
    masked: bool


def is_personal_data(field_class: str) -> bool:
    return field_class in PERSONAL_DATA_FIELD_CLASSES


def apply(
    value: str | None,
    raw_value: str | None,
    crop_uri: str | None,
    *,
    field_class: str,
    role: str,
) -> MaskedTriple:
    """The only place FR-SEC-02's masking decision is made. `role` is
    either an ordinary viewing role (operator/verifier/supervisor/auditor/
    administrator — masked if the field is personal data) or the single
    distinguished value `"auditor_unmasked_read"`, representing the
    separately-logged, purpose-stated unmasked-read operation FR-SEC-08
    requires — never a normal role's default view.

    Callers do not get to mask `value` without also masking `raw_value`
    and `crop_uri`: this function is the boundary, and it either masks
    all three or none.
    """
    if not is_personal_data(field_class) or role in ROLES_THAT_SEE_PERSONAL_DATA_UNMASKED:
        return MaskedTriple(value=value, raw_value=raw_value, crop_uri=crop_uri, masked=False)

    return MaskedTriple(
        value=MASK_TOKEN if value is not None else None,
        raw_value=MASK_TOKEN if raw_value is not None else None,
        crop_uri=None,  # the crop itself is not merely masked — it isn't linked at all (FR-SEC-08)
        masked=True,
    )
