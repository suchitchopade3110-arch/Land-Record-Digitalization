"""GET /closed-sets/{type} domain logic — FR-VAL-09, contract §4.3, P5-05.

The corpus-partition rule ("never serve an LRMS-derived closed set") is
enforced against `ClosedSetEntry.provenance` — a column only a corpus
seeding/ETL process is meant to set — and *never* against `.source`,
which is free-text/self-reported and carries no trust: a row that claims
`source="schema"` while `provenance="lrms"` is exactly the attack T5.c
exists to catch (CLAUDE.md).

"What is the current corpus for this (type[, district])" is itself
config-shaped: a `ConfigVersion` pointer named `closed_set.{type}` (global)
or `closed_set.{type}.{district}` (district-scoped), whose `value` carries
no codes — those live in `ClosedSetEntry`, keyed by `config_version` —
resolved through `libs/config_client` (P5-04) like every other config
value, so a corpus update propagates through the same cache-invalidation
path as everything else config-shaped rather than a bespoke one for this
endpoint alone.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.domain.config_versions import ConfigNotFound
from backend.models.entities import ClosedSetEntry

LAWFUL_PROVENANCES = ("schema", "lgd")  # never "lrms" — FR-VAL-09, the corpus partition rule


class ClosedSetTypeNotFound(Exception):
    """No corpus has ever been configured for this `type` (neither
    district-scoped nor global) — "unknown", not "empty": CLAUDE.md's
    "silence and absence are different answers" applies directly to this
    endpoint (T5.c)."""

    def __init__(self, type_: str):
        self.type = type_
        super().__init__(f"no closed set configured for type={type_!r}")


class InconsistentClosedSetProvenance(Exception):
    """More than one lawful provenance among the entries returned for one
    (type, district, config_version) — a data-integrity bug in how the
    corpus was seeded, not something to silently resolve by picking one,
    matching this repo's posture on every other invariant."""

    def __init__(self, type_: str, provenances: set[str]):
        self.type = type_
        self.provenances = provenances
        super().__init__(f"type={type_!r} has inconsistent lawful provenances among its entries: {sorted(provenances)}")


def config_pointer(type_: str, district: str | None) -> tuple[str, str]:
    """(scope, key) for the `ConfigVersion` pointer naming this corpus —
    district-scoped if `district` is given, global otherwise (a global
    set applies to every district, e.g. a schema-derived enum)."""
    if district:
        return "district", f"closed_set.{type_}.{district}"
    return "global", f"closed_set.{type_}"


def resolve_corpus_config_version(config_client, type_: str, district: str | None) -> str:
    """Resolve the `config_version` currently naming this corpus via
    `libs/config_client` (P5-04) — never a direct DB read, so this
    endpoint gets the same cache/invalidation behaviour as every other
    config consumer. A district-specific pointer is tried first and falls
    back to the global one; if neither has ever been configured, the type
    itself is unknown.
    """
    if district:
        scope, key = config_pointer(type_, district)
        try:
            return config_client.get(scope, key)["config_version"]
        except ConfigNotFound:
            pass  # no district-specific corpus configured — fall back to the global one

    scope, key = config_pointer(type_, None)
    try:
        return config_client.get(scope, key)["config_version"]
    except ConfigNotFound as exc:
        raise ClosedSetTypeNotFound(type_) from exc


def get_closed_set(session: Session, config_client, type_: str, district: str | None) -> dict:
    """The full §4.3 response — `codes, source, config_version` — for
    (`type_`, `district`). Never includes an entry whose `provenance` is
    `"lrms"`, regardless of what that entry's own `.source` claims.
    """
    corpus_version = resolve_corpus_config_version(config_client, type_, district)

    stmt = select(ClosedSetEntry).where(
        ClosedSetEntry.type == type_,
        ClosedSetEntry.config_version == corpus_version,
        ClosedSetEntry.provenance.in_(LAWFUL_PROVENANCES),  # the exclusion keys off this column, never `.source`
    )
    if district:
        stmt = stmt.where(or_(ClosedSetEntry.district == district, ClosedSetEntry.district.is_(None)))
    rows = session.execute(stmt.order_by(ClosedSetEntry.code)).scalars().all()

    codes = [row.code for row in rows]
    provenances = {row.provenance for row in rows}
    if len(provenances) > 1:
        raise InconsistentClosedSetProvenance(type_, provenances)
    # No lawful entries at all (e.g. every seeded row for this corpus is
    # lrms-derived) is a degenerate corpus, not an unknown type — the
    # pointer resolved, so respond with an empty set rather than 404.
    source = next(iter(provenances), "schema")

    return {"codes": codes, "source": source, "config_version": corpus_version}
