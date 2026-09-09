"""P4-04/P4-05 — wires `landaudit`'s pure library pieces (`rollup`,
`witness`, `kms`) to this service's actual configuration: which store
backs the `Witness`, which signer signs roots, how often to roll/anchor.

`anchor_store()` deliberately does NOT reuse `landstorage.get_store()` or
`landstorage.get_secondary_store()` — those are the application's own
primary/secondary object stores, which the same DB-write-capable
credentials this application holds can also read and rewrite. The anchor
store is a THIRD, independent namespace (`ANCHOR_STORE_*` env vars,
`infra/docker-compose.yml`'s `anchor_store` service) that this process
should hold only a write-only append credential to — see PHASE4.md's
"P0 form of the second store" section for exactly what that means and
what could/couldn't be verified about it in this sandbox.
"""
from __future__ import annotations

import os
from functools import lru_cache

from landaudit import (
    LocalSignOnlyKMSStub,
    SecondStoreWitness,
    SignOnlyKeyHandle,
    Witness,
)
from landaudit import perform_roll as _perform_roll
from landstorage import ObjectStorePort
from sqlalchemy.orm import Session

from backend.domain.publication_policy import CONFIG_VERSION


def _build_anchor_store() -> ObjectStorePort:
    driver = os.environ.get("ANCHOR_STORE_DRIVER", "local_fs")
    if driver == "local_fs":
        from landstorage.drivers.local_fs import LocalFsObjectStore

        return LocalFsObjectStore(root=os.environ.get("ANCHOR_STORE_ROOT", "/tmp/landrecords-anchor-store"))
    if driver == "s3":
        from landstorage.drivers.s3 import S3ObjectStore

        return S3ObjectStore(
            bucket=os.environ["ANCHOR_STORE_BUCKET"],
            endpoint_url=os.environ.get("ANCHOR_STORE_URL"),
            access_key_id=os.environ.get("ANCHOR_STORE_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("ANCHOR_STORE_SECRET_ACCESS_KEY"),
        )
    raise ValueError(f"unknown ANCHOR_STORE_DRIVER={driver!r} — expected 'local_fs' or 's3'")


@lru_cache(maxsize=1)
def anchor_store() -> ObjectStorePort:
    return _build_anchor_store()


@lru_cache(maxsize=1)
def get_witness() -> Witness:
    return SecondStoreWitness(anchor_store())


@lru_cache(maxsize=1)
def get_signer() -> SignOnlyKeyHandle:
    return LocalSignOnlyKMSStub()


def perform_scheduled_roll(session: Session, *, anchor: bool = True):
    """The function `backend.workers` schedules on `ROOT_ROLL_INTERVAL`
    (`backend.domain.publication_policy`). Not itself a long-running
    polling loop — same posture Phase 1/2's other workers take (see
    PHASE2.md's "what this phase deliberately did not build": the domain
    logic + an `if __name__ == '__main__'` polling loop exist; a
    production process-supervisor wiring script does not yet)."""
    return _perform_roll(session, witness=get_witness(), signer=get_signer(), config_version=CONFIG_VERSION, anchor=anchor)
