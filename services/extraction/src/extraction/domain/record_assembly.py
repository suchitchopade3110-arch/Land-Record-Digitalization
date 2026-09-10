"""Multi-page record assembly with rationale recorded (FR-EXT-04).

Produces a `RecordAssembly` object linking multiple extractions into a single record group.
Schema compliance enforced against `contracts/schemas/record_assembly.schema.json`.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AssembledField:
    """Represents a merged field across pages, tracking all source extractions and provenance."""

    field_name: str
    canonical_value: str | None
    source_extractions: list[dict[str, Any]]
    is_conflicting: bool = False
    conflicting_values: list[str] = field(default_factory=list)


@dataclass
class AssembledRecordResult:
    """Detailed multi-page assembly result containing contract dict and merged field tracking."""

    assembly_contract: dict[str, Any]
    assembled_fields: dict[str, AssembledField]
    page_order: list[str]
    missing_pages: list[int] = field(default_factory=list)
    has_conflicts: bool = False


def assemble(
    extractions: list[dict] | list[list[dict]],
    record_id: str | None = None,
    strategy: str | None = None,
    rationale: str | None = None,
    actor: str = "system",
    pages_metadata: list[dict[str, Any]] | None = None,
) -> dict:
    """Assembles extractions into a RecordAssembly structure satisfying record_assembly.schema.json."""
    assembled_result = assemble_multi_page_record(
        extractions=extractions,
        record_id=record_id,
        strategy=strategy,
        rationale_override=rationale,
        actor=actor,
        pages_metadata=pages_metadata,
    )
    return assembled_result.assembly_contract


def assemble_multi_page_record(
    extractions: list[dict] | list[list[dict]],
    record_id: str | None = None,
    strategy: str | None = None,
    rationale_override: str | None = None,
    actor: str = "system",
    pages_metadata: list[dict[str, Any]] | None = None,
) -> AssembledRecordResult:
    """Performs multi-page record assembly, grouping, page ordering, duplicate deduplication,
    conflict detection, and rationale logging.
    """
    rec_id = record_id or str(uuid.uuid4())

    # Normalize input extractions into page-level groups
    page_groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []

    if extractions and isinstance(extractions[0], list):
        # List of lists of extractions per page
        for idx, page_exts in enumerate(extractions):  # type: ignore
            meta = (
                pages_metadata[idx]
                if pages_metadata and idx < len(pages_metadata)
                else {"index": idx + 1, "page_id": page_exts[0].get("page_id") if page_exts else f"page_{idx+1}"}
            )
            page_groups.append((meta, page_exts))
    else:
        # Flat list of extractions
        flat_exts: list[dict[str, Any]] = extractions  # type: ignore
        if pages_metadata:
            by_page: dict[str, list[dict[str, Any]]] = {}
            for item in flat_exts:
                p_id = item.get("page_id", "default_page")
                by_page.setdefault(p_id, []).append(item)
            for meta in pages_metadata:
                p_id = meta.get("page_id") or meta.get("id") or "default_page"
                page_groups.append((meta, by_page.get(p_id, [])))
        else:
            by_page = {}
            for item in flat_exts:
                p_id = item.get("page_id", "default_page")
                by_page.setdefault(p_id, []).append(item)
            for idx, (p_id, p_exts) in enumerate(by_page.items()):
                meta = {"index": idx + 1, "page_id": p_id}
                page_groups.append((meta, p_exts))

    # Sort page groups strictly by page index
    page_groups.sort(key=lambda g: int(g[0].get("index") or g[0].get("index_position") or 0))

    # 1. Detect strategy and page sequence gaps
    detected_strategy = strategy
    is_continuation = False
    is_carried_forward = False
    missing_page_indices: list[int] = []
    page_order: list[str] = []
    rationale_lines: list[str] = []

    prev_index: int | None = None
    for meta, _ in page_groups:
        idx = meta.get("index")
        p_id = meta.get("page_id") or meta.get("id") or "unknown_page"
        page_order.append(p_id)

        if meta.get("is_continuation") or meta.get("page_role") in ("continuation", "endorsement"):
            is_continuation = True
        if meta.get("is_carried_forward") or meta.get("page_role") == "carried_forward":
            is_carried_forward = True

        if prev_index is not None and idx is not None and idx > prev_index + 1:
            missing_page_indices.extend(range(prev_index + 1, idx))
        if idx is not None:
            prev_index = idx

    if not detected_strategy:
        if is_carried_forward:
            detected_strategy = "carried_forward"
        elif is_continuation or len(page_groups) > 1:
            detected_strategy = "continuation"
        else:
            detected_strategy = "single_page"

    if detected_strategy not in ("single_page", "continuation", "carried_forward"):
        raise ValueError(f"Invalid assembly strategy: {detected_strategy!r}")

    # Build rationale log header
    num_pages = len(page_groups)
    rationale_lines.append(f"Assembled {num_pages}-page record group (strategy='{detected_strategy}').")
    if missing_page_indices:
        rationale_lines.append(f"[MISSING_PAGE_GAP] Detected missing page(s) in sequence: {missing_page_indices}.")

    # 2. Process extractions across pages to merge fields and detect conflicts
    all_extraction_ids: list[str] = []
    assembled_fields: dict[str, AssembledField] = {}
    has_conflicts = False

    for meta, p_exts in page_groups:
        p_id = meta.get("page_id") or meta.get("id") or "unknown_page"
        p_idx = meta.get("index", 1)

        for ext in p_exts:
            ext_id = ext.get("id")
            if ext_id and ext_id not in all_extraction_ids:
                all_extraction_ids.append(ext_id)

            fn = ext.get("field_name")
            if not fn:
                continue

            raw_val = ext.get("raw_value")
            canon_val = ext.get("canonical_value") or raw_val

            if fn not in assembled_fields:
                assembled_fields[fn] = AssembledField(
                    field_name=fn,
                    canonical_value=canon_val,
                    source_extractions=[ext],
                    is_conflicting=False,
                    conflicting_values=[canon_val] if canon_val else [],
                )
            else:
                af = assembled_fields[fn]
                af.source_extractions.append(ext)

                if canon_val and canon_val not in af.conflicting_values:
                    af.is_conflicting = True
                    af.conflicting_values.append(canon_val)
                    has_conflicts = True
                    rationale_lines.append(
                        f"[CONFLICT] Field '{fn}' has conflicting values across pages: "
                        f"{af.conflicting_values}. Preserving all source readings with page provenance."
                    )
                else:
                    rationale_lines.append(
                        f"[DUPLICATE] Merged duplicate field '{fn}' value '{canon_val}' from page {p_id} (index {p_idx})."
                    )

    if rationale_override:
        final_rationale = rationale_override
    else:
        final_rationale = " ".join(rationale_lines)

    contract_dict = {
        "id": str(uuid.uuid4()),
        "record_id": rec_id,
        "extraction_ids": all_extraction_ids,
        "strategy": detected_strategy,
        "rationale": final_rationale,
        "actor": actor,
    }

    return AssembledRecordResult(
        assembly_contract=contract_dict,
        assembled_fields=assembled_fields,
        page_order=page_order,
        missing_pages=missing_page_indices,
        has_conflicts=has_conflicts,
    )
