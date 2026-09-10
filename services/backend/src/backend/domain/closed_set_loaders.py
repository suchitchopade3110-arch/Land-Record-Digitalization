"""P5-05-fix — the only two sanctioned writers of `ClosedSetEntry` rows
(FR-VAL-09, contract §4.3).

Before this module existed there was no loader at all: `provenance` was a
plain `NOT NULL` column any caller constructing a `ClosedSetEntry` could
set directly, same as the untrusted `.source` field — the corpus-partition
rule's "ground truth" column had no more protection than the column it was
supposed to be trusted over. That gap is what this module and
`infra/migrations/versions/0008_closed_set_entry_loader_provenance.py`
close together.

Neither function below accepts `provenance` as a parameter — each
hardcodes its own and sets the matching Postgres session GUC
(`app.closed_set_loader_provenance`, scoped to the current transaction
only) immediately before inserting, so 0008's trigger can verify a
`'schema'`- or `'lgd'`-provenance row actually came from the loader that
claims to have written it. There is no third function here that takes
`provenance` as an argument — adding one would silently reopen exactly
the gap this module exists to close.
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.models.entities import ClosedSetEntry

_TRUSTED_PROVENANCES = ("schema", "lgd")  # the only values this module ever hardcodes and sets


def _set_loader_provenance(session: Session, provenance: str) -> None:
    # SET LOCAL, not SET — it must not survive past this transaction's
    # COMMIT/ROLLBACK, so a later, unrelated write on a pooled connection
    # is never accidentally covered by a prior loader's GUC. Postgres's
    # SET command does not accept a bind parameter for the value, so this
    # asserts against a closed, hardcoded whitelist first — `provenance`
    # is always a literal passed by this module's own two callers below,
    # never external input — rather than interpolating an arbitrary string.
    assert provenance in _TRUSTED_PROVENANCES, f"refusing to SET LOCAL an unrecognised provenance: {provenance!r}"
    session.execute(text(f"SET LOCAL app.closed_set_loader_provenance = '{provenance}'"))


def load_schema_derived_entries(
    session: Session,
    *,
    type_: str,
    config_version: str,
    codes: Sequence[str],
    district: str | None = None,
    source: str = "schema_extractor",
) -> list[ClosedSetEntry]:
    """The only sanctioned way to write `provenance='schema'` rows —
    called by the schema-derived closed-set extractor (enum values baked
    into `contracts/schemas/*.json`), never by a district-specific
    importer."""
    _set_loader_provenance(session, "schema")
    rows = [
        ClosedSetEntry(
            type=type_, district=district, code=code, source=source,
            provenance="schema", config_version=config_version,
        )
        for code in codes
    ]
    session.add_all(rows)
    session.flush()
    return rows


def load_lgd_derived_entries(
    session: Session,
    *,
    type_: str,
    config_version: str,
    codes: Sequence[str],
    district: str | None = None,
    source: str = "lgd_importer",
) -> list[ClosedSetEntry]:
    """The only sanctioned way to write `provenance='lgd'` rows — called
    by the Local Government Directory importer."""
    _set_loader_provenance(session, "lgd")
    rows = [
        ClosedSetEntry(
            type=type_, district=district, code=code, source=source,
            provenance="lgd", config_version=config_version,
        )
        for code in codes
    ]
    session.add_all(rows)
    session.flush()
    return rows
