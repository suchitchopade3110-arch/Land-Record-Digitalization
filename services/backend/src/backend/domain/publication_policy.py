"""Phase 4 policy values — ground rule 4, "config, not constants." Same
posture as `backend.domain.review_policy` (Phase 3): every value below is
meant to read from M13's `ConfigVersion` table
(scope=`global`, the keys named below); that service doesn't serve real
values yet (`backend.config._fetch_from_db` still `NotImplementedError`,
Phase 5), so this module is the fixture standing in for it — one place,
one shape, so the eventual swap changes where a caller gets a value, never
its shape or how many places read it.

These are exactly the values PHASE4.md's "root roll interval, anchor
cadence" section records — keep the two in sync if either changes.
"""
from __future__ import annotations

from datetime import timedelta

CONFIG_VERSION = "phase4-fixture-v1"  # stands in for a real ConfigVersion.id

# key: "audit.root_roll_interval" — P4-04/FR-PUB-09. How often the global
# Merkle root over shard heads is recomputed and persisted as a
# `chain_root` row.
ROOT_ROLL_INTERVAL = timedelta(hours=1)

# key: "audit.anchor_cadence" — P4-05/FR-PUB-08. How often a rolled root
# is actually sent to the external `Witness`, as a multiple of
# `ROOT_ROLL_INTERVAL`. 1 means "anchor every roll" (this P0's default —
# see PHASE4.md for why a coarser cadence was considered and rejected for
# pilot scale: the anchoring call is cheap relative to the roll itself,
# and anchoring less often than rolling only widens T4.a's tamper window
# for no operational benefit at this volume).
ANCHOR_CADENCE_ROLLS = 1

# key: "review.crop_url_ttl_seconds" — pre-existing, review_policy.py's
# own value (P3-04); P4 reuses it for the masked-crop path (P4-10), never
# redefines a second TTL for the same concept.

# key: "rbac.permission_matrix_version" — a version tag for
# `backend.domain.access_control.PERMISSION_MATRIX`'s content, so a
# permission-matrix change is itself something `ConfigVersion` can pin and
# audit (FR-CFG-02: nothing downstream resolves "current" permissions
# outside of what was pinned) once the matrix moves to real config. At P0
# the matrix is a Python constant, not yet config-driven — this is the
# named placeholder for when it becomes one.
PERMISSION_MATRIX_VERSION = "phase6-t1-01-v1"

# key: "publication.training_store_read_permission" — the training-store
# bypass decision (see PHASE4.md): Tharun's modelwork reads `correction`
# directly, under an explicit exemption naming FR-SEC-05/FR-SEC-09 as the
# controls, gated by `backend.domain.access_control.Permission.TRAINING_STORE_READ`
# rather than left as an unauthenticated table read.
TRAINING_STORE_EXEMPTION_CONTROLS = ("FR-SEC-05", "FR-SEC-09")
