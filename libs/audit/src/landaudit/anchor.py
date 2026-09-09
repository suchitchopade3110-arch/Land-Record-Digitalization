"""Compatibility shim. `AnchorWitness`/`LocalSecondStoreWitness` were this
module's names before P4-05 renamed the interface to `Witness`
(`witness.py`) to match the build prompt's naming and split the KMS
sign-only handle out into its own module (`kms.py`). Nothing outside
`landaudit/__init__.py` imported these names directly (checked via
`grep -rn AnchorWitness` across the repo before this rename), so this
shim exists only as a courtesy against an import written from memory of
the old name, not because any real caller needs it.

`compute_global_root` similarly moves to `landaudit.merkle` — P4-04
replaces the flat "hash the concatenation of shard heads" scheme this
function used with a real Merkle tree over shard heads
(`landaudit.merkle.roll_global_root`), because a flat hash cannot support
"name which shard heads were included so a verifier can reconstruct the
tree without trusting the stored root" the way a tree's own structure
can. The two are not numerically compatible — do not use
`compute_global_root`'s old output as an anchored root going forward.
"""
from __future__ import annotations

from landaudit.merkle import compute_root as _compute_root
from landaudit.merkle import current_shard_heads as _current_shard_heads
from landaudit.witness import SecondStoreWitness as LocalSecondStoreWitness
from landaudit.witness import Witness as AnchorWitness
from landaudit.witness import WitnessReceipt as AnchorReceipt

__all__ = ["AnchorReceipt", "AnchorWitness", "LocalSecondStoreWitness", "compute_global_root"]


def compute_global_root(session) -> str:
    """Deprecated — see this module's docstring. Kept only so an old
    caller does not hard-fail; prefer `landaudit.merkle.roll_global_root`.
    """
    return _compute_root(_current_shard_heads(session))
