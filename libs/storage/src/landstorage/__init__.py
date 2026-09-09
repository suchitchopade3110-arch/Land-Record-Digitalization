"""ADR-004 — `get_store()` is the one call every service makes to obtain an
`ObjectStorePort`. Driver selection is by `OBJECT_STORE_DRIVER` env var.

`get_secondary_store()` (FR-ING-07, P2-06) obtains a second store from a
parallel `SECONDARY_OBJECT_STORE_*` env var namespace, deliberately never
sharing config with the primary — see `landstorage.distinct` for the check
that the two are actually independent, not just two names for the same
bucket/root/credential.
"""
from __future__ import annotations

import os
from functools import lru_cache

from landstorage.distinct import (
    DistinctCredentialsError,
    assert_distinct_stores,
    store_identity_from_env,
)
from landstorage.port import (
    ObjectAlreadyExistsWithDifferentContent,
    ObjectStorePort,
    PutResult,
    digest_of,
    hash_stream_to_spooled_tempfile,
    key_for,
)

__all__ = [
    "DistinctCredentialsError",
    "ObjectAlreadyExistsWithDifferentContent",
    "ObjectStorePort",
    "PutResult",
    "assert_distinct_stores",
    "digest_of",
    "get_secondary_store",
    "get_store",
    "hash_stream_to_spooled_tempfile",
    "key_for",
    "store_identity_from_env",
]


def _build_from_env(prefix: str) -> ObjectStorePort:
    driver = os.environ.get(f"{prefix}_DRIVER", "local_fs")
    if driver == "local_fs":
        from landstorage.drivers.local_fs import LocalFsObjectStore

        return LocalFsObjectStore(root=os.environ.get(f"{prefix}_ROOT", "/tmp/landrecords-objects"))
    if driver == "s3":
        from landstorage.drivers.s3 import S3ObjectStore

        return S3ObjectStore(
            bucket=os.environ[f"{prefix}_BUCKET"],
            endpoint_url=os.environ.get(f"{prefix}_URL"),
            access_key_id=os.environ.get(f"{prefix}_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get(f"{prefix}_SECRET_ACCESS_KEY"),
        )
    raise ValueError(f"unknown {prefix}_DRIVER={driver!r} — expected 'local_fs' or 's3'")


@lru_cache(maxsize=1)
def get_store() -> ObjectStorePort:
    return _build_from_env("OBJECT_STORE")


@lru_cache(maxsize=1)
def get_secondary_store() -> ObjectStorePort:
    """The independently-credentialed second copy FR-ING-07's fixity sweep
    restores from — a single compromised primary-store key must not be
    able to rewrite both copies. Configured entirely separately from
    `get_store()` (`SECONDARY_OBJECT_STORE_*`, not derived from
    `OBJECT_STORE_*` in any way) so there is no code path that could make
    the two collapse into the same credential by accident.
    """
    return _build_from_env("SECONDARY_OBJECT_STORE")
