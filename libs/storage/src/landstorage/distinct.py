"""FR-ING-07 (P2-06) — "a single compromised key must not be able to
rewrite both copies." That property only holds if the primary and
secondary stores actually resolve to two different locations under two
different credential sets — nothing about `ObjectStorePort` itself
prevents someone from pointing `SECONDARY_OBJECT_STORE_*` at the exact
same bucket/root/credential as `OBJECT_STORE_*` by mistake, so this module
checks it explicitly, in config validation, rather than trusting deploy
configuration to get it right silently.
"""
from __future__ import annotations

import os


class DistinctCredentialsError(ValueError):
    """Raised when the primary and secondary store configs are not
    distinct — same driver, same root/bucket, and (for s3) the same
    endpoint and access key id. Two stores that happen to share a driver
    but point at different roots/buckets/keys are fine; this only catches
    the case where they'd actually be the same access path."""


def store_identity_from_env(prefix: str) -> dict[str, str | None]:
    """The identifying (non-secret) parts of a store's config — enough to
    tell two configs apart, without needing the secret key itself (which
    this module never reads or logs)."""
    driver = os.environ.get(f"{prefix}_DRIVER", "local_fs")
    if driver == "local_fs":
        return {"driver": driver, "root": os.environ.get(f"{prefix}_ROOT", "/tmp/landrecords-objects")}
    if driver == "s3":
        return {
            "driver": driver,
            "bucket": os.environ.get(f"{prefix}_BUCKET"),
            "endpoint_url": os.environ.get(f"{prefix}_URL"),
            "access_key_id": os.environ.get(f"{prefix}_ACCESS_KEY_ID"),
        }
    raise ValueError(f"unknown {prefix}_DRIVER={driver!r} — expected 'local_fs' or 's3'")


def assert_distinct_stores(primary_prefix: str = "OBJECT_STORE", secondary_prefix: str = "SECONDARY_OBJECT_STORE") -> None:
    """Raise `DistinctCredentialsError` if the two env-var namespaces
    resolve to the same store identity. Call this at service startup
    (config validation, per P2-06's "verified as distinct in config
    validation") and before running the fixity sweep — never assume a
    deploy's environment was configured correctly.
    """
    primary = store_identity_from_env(primary_prefix)
    secondary = store_identity_from_env(secondary_prefix)
    if primary == secondary:
        raise DistinctCredentialsError(
            f"{primary_prefix}_* and {secondary_prefix}_* resolve to the same store "
            f"({primary!r}) — FR-ING-07 requires two genuinely independent copies, "
            "not one location checked twice."
        )
