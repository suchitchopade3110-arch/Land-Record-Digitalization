"""P0 Relationship Extraction (Owner ↔ Share ↔ Parcel) (FR-EXT-02).

Represents relationships between extracted land record entities according to the project's
contracts and architecture without violating `extraction.schema.json`'s `additionalProperties: false`.

Binds:
- Parcel / Survey number
- Owner(s) + Familial relationship ("s/o Mohan Lal")
- Share fraction (numerator / denominator)
"""
from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class EntityRef:
    extraction_id: str
    field_name: str
    raw_value: str
    canonical_value: str | None
    bbox: dict | None
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OwnerRef(EntityRef):
    relationship_label: str | None = None


@dataclass
class ShareRef:
    extraction_id: str | None
    raw_value: str | None
    canonical_value: str | None
    share_numerator: int | None
    share_denominator: int | None
    bbox: dict | None
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OwnerParcelShareBinding:
    """Canonical relationship representation for land record entity triples."""

    binding_id: str
    page_id: str
    parcel: EntityRef | None
    owners: list[OwnerRef]
    share: ShareRef | None
    evidence_type: str  # "spatial_proximity" | "section_header" | "row_alignment" | "fallback"
    confidence: float
    is_ambiguous: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "page_id": self.page_id,
            "parcel": self.parcel.to_dict() if self.parcel else None,
            "owners": [o.to_dict() for o in self.owners],
            "share": self.share.to_dict() if self.share else None,
            "evidence_type": self.evidence_type,
            "confidence": self.confidence,
            "is_ambiguous": self.is_ambiguous,
        }


def extract_relationships(
    extractions: list[dict[str, Any]],
    page_id: str | None = None,
) -> list[dict[str, Any]]:
    """Infers structural relationships between owner names, shares, and survey numbers.

    Returns a list of `OwnerParcelShareBinding` dict representations while leaving input
    `extractions` unmodified and 100% schema compliant.
    """
    target_page_id = page_id or (extractions[0].get("page_id") if extractions else "00000000-0000-0000-0000-000000000000")
    if not extractions:
        return []

    # Partition extractions by field type
    parcels: list[dict[str, Any]] = []
    owners: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    shares: list[dict[str, Any]] = []

    for item in extractions:
        fn = item.get("field_name")
        if fn == "survey_number":
            parcels.append(item)
        elif fn == "owner_name":
            owners.append(item)
        elif fn == "relationship":
            relationships.append(item)
        elif fn == "share_fraction":
            shares.append(item)

    # 1. Single Parcel / Single Owner Case
    if len(parcels) <= 1 and len(owners) <= 1:
        return _build_single_parcel_bindings(
            page_id=target_page_id,
            parcels=parcels,
            owners=owners,
            relationships=relationships,
            shares=shares,
        )

    # 2. Section / Spatial Layout Segmentation for Multiple Parcels
    return _build_multi_parcel_bindings(
        page_id=target_page_id,
        parcels=parcels,
        owners=owners,
        relationships=relationships,
        shares=shares,
        all_extractions=extractions,
    )


def _build_single_parcel_bindings(
    page_id: str,
    parcels: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    shares: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Handles 1 parcel with 1 or multiple owners (shared parcel)."""
    parcel_ref = _to_entity_ref(parcels[0]) if parcels else None
    share_ref = _to_share_ref(shares[0]) if shares else None

    rel_label = relationships[0].get("raw_value") if relationships else None

    owner_refs = []
    for o in owners:
        o_ref = OwnerRef(
            extraction_id=o["id"],
            field_name="owner_name",
            raw_value=o["raw_value"],
            canonical_value=o.get("canonical_value"),
            bbox=o.get("bbox"),
            confidence=float(o.get("token_confidence", 1.0)),
            relationship_label=rel_label,
        )
        owner_refs.append(o_ref)

    # Determine confidence and ambiguity
    conf = _calculate_binding_confidence(parcel_ref, owner_refs, share_ref)
    is_ambiguous = False
    if len(parcels) == 0 or len(owners) == 0:
        is_ambiguous = True

    binding = OwnerParcelShareBinding(
        binding_id=str(uuid.uuid4()),
        page_id=page_id,
        parcel=parcel_ref,
        owners=owner_refs,
        share=share_ref,
        evidence_type="spatial_proximity" if parcel_ref and owner_refs else "fallback",
        confidence=conf,
        is_ambiguous=is_ambiguous,
    )
    return [binding.to_dict()]


def _build_multi_parcel_bindings(
    page_id: str,
    parcels: list[dict[str, Any]],
    owners: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    shares: list[dict[str, Any]],
    all_extractions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Handles multi-parcel pages using vertical layout coordinate bounds & evidence distance."""
    # Sort parcels by vertical Y position
    sorted_parcels = sorted(parcels, key=lambda p: _get_y_center(p.get("bbox")))
    bindings: list[dict[str, Any]] = []

    # Map each owner to nearest vertical parcel block
    for i, p_item in enumerate(sorted_parcels):
        p_y = _get_y_center(p_item.get("bbox"))
        p_next_y = _get_y_center(sorted_parcels[i + 1].get("bbox")) if i + 1 < len(sorted_parcels) else float("inf")

        # Find owners belonging to this vertical band [p_y - 20, p_next_y)
        associated_owners = []
        for o in owners:
            o_y = _get_y_center(o.get("bbox"))
            if p_y - 20.0 <= o_y < p_next_y:
                rel_label = _find_nearest_relationship(o, relationships)
                o_ref = OwnerRef(
                    extraction_id=o["id"],
                    field_name="owner_name",
                    raw_value=o["raw_value"],
                    canonical_value=o.get("canonical_value"),
                    bbox=o.get("bbox"),
                    confidence=float(o.get("token_confidence", 1.0)),
                    relationship_label=rel_label,
                )
                associated_owners.append(o_ref)

        # Find share associated with this band
        associated_share = None
        for s in shares:
            s_y = _get_y_center(s.get("bbox"))
            if p_y - 20.0 <= s_y < p_next_y:
                associated_share = _to_share_ref(s)
                break

        p_ref = _to_entity_ref(p_item)
        conf = _calculate_binding_confidence(p_ref, associated_owners, associated_share)
        is_ambiguous = len(associated_owners) == 0

        # High distance or unaligned items increase ambiguity
        if len(sorted_parcels) > 1 and len(associated_owners) > 2 and not associated_share:
            is_ambiguous = True

        b = OwnerParcelShareBinding(
            binding_id=str(uuid.uuid4()),
            page_id=page_id,
            parcel=p_ref,
            owners=associated_owners,
            share=associated_share,
            evidence_type="section_header" if not is_ambiguous else "fallback",
            confidence=conf if not is_ambiguous else round(conf * 0.7, 2),
            is_ambiguous=is_ambiguous,
        )
        bindings.append(b.to_dict())

    # Check for unassigned owners (ambiguous association)
    unassigned_owners = []
    for o in owners:
        o_y = _get_y_center(o.get("bbox"))
        first_p_y = _get_y_center(sorted_parcels[0].get("bbox")) if sorted_parcels else 0.0
        if o_y < first_p_y - 20.0:
            rel_label = _find_nearest_relationship(o, relationships)
            o_ref = OwnerRef(
                extraction_id=o["id"],
                field_name="owner_name",
                raw_value=o["raw_value"],
                canonical_value=o.get("canonical_value"),
                bbox=o.get("bbox"),
                confidence=float(o.get("token_confidence", 1.0)),
                relationship_label=rel_label,
            )
            unassigned_owners.append(o_ref)

    if unassigned_owners:
        ambiguous_b = OwnerParcelShareBinding(
            binding_id=str(uuid.uuid4()),
            page_id=page_id,
            parcel=None,
            owners=unassigned_owners,
            share=None,
            evidence_type="fallback",
            confidence=0.5,
            is_ambiguous=True,
        )
        bindings.append(ambiguous_b.to_dict())

    return bindings


def _to_entity_ref(item: dict[str, Any]) -> EntityRef:
    return EntityRef(
        extraction_id=item["id"],
        field_name=item["field_name"],
        raw_value=item["raw_value"],
        canonical_value=item.get("canonical_value"),
        bbox=item.get("bbox"),
        confidence=float(item.get("token_confidence", 1.0)),
    )


def _to_share_ref(item: dict[str, Any]) -> ShareRef:
    raw_val = item.get("raw_value", "")
    num, den = _parse_fraction(raw_val or item.get("canonical_value", ""))
    return ShareRef(
        extraction_id=item["id"],
        raw_value=raw_val,
        canonical_value=item.get("canonical_value"),
        share_numerator=num,
        share_denominator=den,
        bbox=item.get("bbox"),
        confidence=float(item.get("token_confidence", 1.0)),
    )


def _parse_fraction(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    match = re.search(r"(\d+)/(\d+)", text)
    if match:
        try:
            n, d = int(match.group(1)), int(match.group(2))
            if d != 0:
                return n, d
        except ValueError:
            pass
    return None, None


def _find_nearest_relationship(owner_item: dict[str, Any], relationships: list[dict[str, Any]]) -> str | None:
    if not relationships:
        return None
    o_y = _get_y_center(owner_item.get("bbox"))
    best_rel = None
    min_dist = float("inf")

    for r in relationships:
        r_y = _get_y_center(r.get("bbox"))
        dist = abs(o_y - r_y)
        if dist < min_dist and dist < 50.0:  # Within 50px vertical threshold
            min_dist = dist
            best_rel = r.get("raw_value")

    return best_rel


def _get_y_center(bbox: dict[str, Any] | None) -> float:
    if not bbox:
        return 0.0
    return float(bbox.get("y", 0.0)) + float(bbox.get("h", 0.0)) / 2.0


def _calculate_binding_confidence(
    parcel: EntityRef | None,
    owners: list[OwnerRef],
    share: ShareRef | None,
) -> float:
    confs = []
    if parcel:
        confs.append(parcel.confidence)
    for o in owners:
        confs.append(o.confidence)
    if share:
        confs.append(share.confidence)

    if not confs:
        return 0.0
    # Mean confidence rounded to 2 decimals
    return round(sum(confs) / len(confs), 2)
