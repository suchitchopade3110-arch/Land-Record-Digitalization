"""ADR-004 — `get_store()` is the one call every service makes to obtain an
`ObjectStorePort`. Driver selection is by `OBJECT_STORE_DRIVER` env var.
"""
from __future__ import annotations

import os
from functools import lru_cache

from landstorage.port import (
    ObjectAlreadyExistsWithDifferentContent,
    ObjectStorePort,
    PutResult,
    digest_of,
    key_for,
)

__all__ = [
    "ObjectAlreadyExistsWithDifferentContent",
    "ObjectStorePort",
    "PutResult",
    "digest_of",
    "key_for",
    "get_store",
]


@lru_cache(maxsize=1)
def get_store() -> ObjectStorePort:
    driver = os.environ.get("OBJECT_STORE_DRIVER", "local_fs")
    if driver == "local_fs":
        from landstorage.drivers.local_fs import LocalFsObjectStore

        return LocalFsObjectStore(root=os.environ.get("OBJECT_STORE_ROOT", "/tmp/landrecords-objects"))
    if driver == "s3":
        from landstorage.drivers.s3 import S3ObjectStore

        return S3ObjectStore(
            bucket=os.environ["OBJECT_STORE_BUCKET"],
            endpoint_url=os.environ.get("OBJECT_STORE_URL"),
        )
    raise ValueError(f"unknown OBJECT_STORE_DRIVER={driver!r} — expected 'local_fs' or 's3'")
