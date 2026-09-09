"""Phase 3 policy values — ground rule 5, "config, not constants." Every
threshold/field-class list/blocking-state set/TTL this band uses is meant
to read from M13's `ConfigVersion` table (FR-CFG-01/02), scope=`global`,
the keys named below. That service doesn't serve real values yet
(`backend.config._fetch_from_db` is still `NotImplementedError` — Phase 5,
per PHASE2.md and `backend.domain.unit_table`'s identical situation), so
this module is the fixture standing in for it: one place, one shape, so
the eventual swap to a real `config_client.get(scope, key)` call changes
where a caller gets its value, never what the value's shape is or how
many places read it. Every fixture below is exactly the value recorded in
PHASE3.md's "maker-checker threshold defaults" / "crop URL TTL" /
"blocking-state set" sections — keep the two in sync if either changes.
"""
from __future__ import annotations

from datetime import timedelta

CONFIG_VERSION = "phase3-fixture-v1"  # stands in for a real ConfigVersion.id

# key: "maker_checker.edit_distance_threshold" — a correction whose edit
# distance from the model's reading exceeds this, on a field in
# MAKER_CHECKER_FIELD_CLASSES, requires a second, distinct actor before it
# takes effect (FR-REV-12).
MAKER_CHECKER_EDIT_DISTANCE_THRESHOLD = 4

# key: "maker_checker.field_classes" — the PRD/architecture's own list
# (§16): owner name, share fraction, area, survey number. `field_class`
# values here are expected to match `Extraction.field_name` exactly —
# this repo's P0 schema has no separate field-class taxonomy (single
# pilot document type), same note as `serializers.mask_extraction_for_role`.
MAKER_CHECKER_FIELD_CLASSES = frozenset({"owner_name", "share_fraction", "area", "survey_number"})

# key: "review.crop_url_ttl_seconds" — FR-REV-01/FR-SEC-08. Short enough
# that a leaked URL is useless well before an officer would plausibly
# still be looking at the same task.
CROP_URL_TTL_SECONDS = 120

# key: "novelty.cluster_alert_dedup_window" — FR-CNF-14. A second
# extraction from the same novelty cluster within this window is folded
# into the existing alert (its id appended), not a new alert.
NOVELTY_CLUSTER_ALERT_DEDUP_WINDOW = timedelta(hours=6)

# key: "conflict.blocking_states" — FR-CFL-03. Every `Conflict.state`
# that blocks publication. Deliberately not `state != 'resolved'`
# spelled out at each call site (that was the P2-08 gate's original
# shortcut); this is the one place the set lives, so a future decision to
# also block on some state that isn't `referred` doesn't require finding
# every gate that hard-coded the complement.
BLOCKING_CONFLICT_STATES = frozenset({"open", "under_enquiry", "referred"})

# key: "review.session_gap" — P3-13/FR-REV-16. An officer's claim after
# this much inactivity starts a new ReviewSession rather than continuing
# the last one.
REVIEW_SESSION_GAP = timedelta(hours=1)
