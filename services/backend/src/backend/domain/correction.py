"""P3-07/P3-08 — FR-REV-12, FR-LRN-01/07/11. Correction write on officer
submit, with maker-checker held on high-edit-distance, high-value-field
corrections.

`source_page_digest` (FR-LRN-11, Tharun's leakage-guard key) is read off
`Page.storage_uri` — the content-addressed key `landstorage` assigned at
ingest time (`sha256/xx/yy/<digest>`) — never recomputed from the crop.
A wrong write here makes Tharun's leakage guard pass silently on a page
it should have excluded, so this is the one value in this module that is
read from exactly one place, not derived.
"""
from __future__ import annotations

from datetime import datetime, timezone

from landaudit import append as audit_append
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.review_policy import (
    MAKER_CHECKER_EDIT_DISTANCE_THRESHOLD,
    MAKER_CHECKER_FIELD_CLASSES,
)
from backend.models.entities import (
    Correction,
    Extraction,
    Page,
    PendingCorrection,
    ReviewTask,
)


class SameActorCannotConfirm(ValueError):
    """FR-REV-12's whole point — the maker and the checker must be two
    distinct people."""


class PendingCorrectionAlreadyResolved(ValueError):
    pass


def edit_distance(a: str, b: str) -> int:
    """Plain Levenshtein distance. Field values here are short strings
    (names, survey numbers, share fractions) — O(len(a)*len(b)) with a
    two-row DP is more than fast enough and needs no dependency."""
    a, b = a or "", b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def source_page_digest_for(page: Page) -> str:
    """The digest embedded in the page's own content-addressed storage
    key (`landstorage.port.key_for`: `sha256/{d[0:2]}/{d[2:4]}/{digest}`)
    — the ingest-time value, not a recomputation. Raises if the page has
    no `storage_uri` yet (page split hasn't stored an image for it) — a
    correction cannot be honestly attributed to a page with no recorded
    source object."""
    if not page.storage_uri:
        raise ValueError(f"Page {page.id} has no storage_uri — cannot derive source_page_digest")
    return page.storage_uri.rsplit("/", 1)[-1]


def submit_correction(
    session: Session, *, extraction: Extraction, corrected: str, actor: str, stream: str
) -> Correction | PendingCorrection:
    """FR-LRN-01/07 — write the correction. FR-REV-12 — if the field is a
    maker-checker class and the edit distance exceeds the configured
    threshold, land it in `PendingCorrection` instead of `Correction`
    (never both): the record is durable, but not yet a `Correction`, and
    is therefore structurally absent from Tharun's training-store read
    contract (which only ever queries `correction`) until a second actor
    confirms it.
    """
    page = session.get(Page, extraction.page_id)
    digest = source_page_digest_for(page)
    predicted = extraction.canonical_value or extraction.raw_value
    distance = edit_distance(predicted, corrected)
    field_class = extraction.field_name

    needs_maker_checker = (
        field_class in MAKER_CHECKER_FIELD_CLASSES and distance > MAKER_CHECKER_EDIT_DISTANCE_THRESHOLD
    )

    if needs_maker_checker:
        pending = PendingCorrection(
            extraction_id=extraction.id,
            crop_uri=f"/pages/{extraction.page_id}/crop?bbox={extraction.bbox}",
            predicted=predicted,
            corrected=corrected,
            edit_distance=distance,
            field_class=field_class,
            first_actor=actor,
            model_version=extraction.model_version,
            config_version=extraction.config_version,
            stream=stream,
            source_page_digest=digest,
        )
        session.add(pending)
        session.flush()
        audit_append(
            session, actor=actor, action="correction.pending_maker_checker",
            subject=extraction.id, purpose="maker_checker_hold",
        )
        return pending

    correction = Correction(
        extraction_id=extraction.id,
        crop_uri=f"/pages/{extraction.page_id}/crop?bbox={extraction.bbox}",
        predicted=predicted,
        corrected=corrected,
        edit_distance=distance,
        actor=actor,
        model_version=extraction.model_version,
        config_version=extraction.config_version,
        stream=stream,
        source_page_digest=digest,
    )
    session.add(correction)
    session.flush()
    audit_append(session, actor=actor, action="correction.submitted", subject=extraction.id, purpose="review_submit")
    return correction


def confirm_pending_correction(session: Session, pending_id: str, *, actor: str) -> Correction:
    """FR-REV-12 — the second, distinct actor's action. Only now does a
    real `Correction` row exist; only from this point on is the edit
    visible to the publish path or the training-store read contract.
    D2 rule: confirmer != maker, confirmer != task claimant."""
    pending = session.get(PendingCorrection, pending_id)
    if pending is None:
        raise KeyError(f"no PendingCorrection with id={pending_id!r}")
    if pending.state != "pending":
        raise PendingCorrectionAlreadyResolved(f"PendingCorrection {pending_id} is already {pending.state}")

    # Check claimant of review task for this extraction
    claimant = None
    task = session.execute(
        select(ReviewTask).where(ReviewTask.extraction_id == pending.extraction_id)
    ).scalars().first()
    if task is not None:
        claimant = task.assignee

    if actor == pending.first_actor or (claimant is not None and actor == claimant):
        audit_append(
            session, actor=actor, action="correction.confirm_rejected",
            subject=pending.extraction_id, purpose="maker_checker_confirm",
        )
        session.flush()
        raise SameActorCannotConfirm(
            f"actor {actor!r} cannot confirm correction submitted by {pending.first_actor!r} or claimed by {claimant!r} (FR-REV-12)"
        )

    correction = Correction(
        extraction_id=pending.extraction_id,
        crop_uri=pending.crop_uri,
        predicted=pending.predicted,
        corrected=pending.corrected,
        edit_distance=pending.edit_distance,
        actor=actor,
        model_version=pending.model_version,
        config_version=pending.config_version,
        stream=pending.stream,
        source_page_digest=pending.source_page_digest,
    )
    session.add(correction)
    session.flush()

    pending.state = "confirmed"
    pending.confirmed_by = actor
    pending.confirmed_at = datetime.now(timezone.utc)
    pending.resulting_correction_id = correction.id
    session.flush()

    audit_append(
        session, actor=actor, action="correction.confirmed",
        subject=pending.extraction_id, purpose="maker_checker_confirm",
    )
    return correction
