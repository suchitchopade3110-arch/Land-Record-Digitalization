"""ADR-004 — the one interface every service uses to store or retrieve an
immutable, content-addressed object (originals: FR-ING-02; correction
crops: FR-LRN-01/FR-SEC-09; anything else that must be traceable back to
exact bytes: FR-PUB-02).
"""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass


def digest_of(data: bytes) -> str:
    """The one place SHA-256 is computed for storage keys — every put()
    and every independent-credential fixity check (FR-ING-07) calls this,
    never `hashlib.sha256` inline, so there is exactly one digest
    algorithm in the system to ever change."""
    return hashlib.sha256(data).hexdigest()


def key_for(digest: str) -> str:
    """`sha256/{d[0:2]}/{d[2:4]}/{digest}` — see ADR-004."""
    return f"sha256/{digest[0:2]}/{digest[2:4]}/{digest}"


@dataclass(frozen=True)
class PutResult:
    key: str
    digest: str
    created: bool  # False => this digest already existed (FR-ING-04 dedupe)


class ObjectAlreadyExistsWithDifferentContent(Exception):
    """Raised if a write would land at an existing key with different bytes
    than what's stored there. Should be unreachable in normal operation
    (the key IS the digest of the bytes) — reaching it means the digest
    function itself produced two different outputs for the same input,
    which is a bug in the storage layer worth failing loudly over, per
    ADR-004."""


class ObjectStorePort(abc.ABC):
    @abc.abstractmethod
    def put(self, data: bytes) -> PutResult:
        """Store `data` at its content-addressed key. Idempotent: a second
        put() of identical bytes returns the same key with `created=False`
        and performs no additional write (FR-ING-04)."""

    @abc.abstractmethod
    def get(self, key: str) -> bytes:
        """Retrieve the object at `key`. Raises `KeyError` if absent."""

    @abc.abstractmethod
    def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    def verify_fixity(self, key: str) -> bool:
        """Re-hash the stored object and compare against the digest
        encoded in its own key (FR-ING-07). Returns False on mismatch —
        callers (the scheduled fixity sweep) raise the FR-ING-08-style
        dashboard alert; this method itself never raises on a mismatch,
        only on the object being entirely unreadable."""

    # There is deliberately no delete() or overwrite() on this port —
    # FR-ING-02 originals are immutable and never deleted by the pipeline.
    # A retention/erasure path (FR-SEC-06, P1) is a distinct, audited
    # operation and does not belong on the general-purpose port every
    # service imports.
