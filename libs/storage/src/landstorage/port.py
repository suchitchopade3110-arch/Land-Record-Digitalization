"""ADR-004 — the one interface every service uses to store or retrieve an
immutable, content-addressed object (originals: FR-ING-02; correction
crops: FR-LRN-01/FR-SEC-09; anything else that must be traceable back to
exact bytes: FR-PUB-02).
"""
from __future__ import annotations

import abc
import hashlib
import tempfile
from dataclasses import dataclass
from typing import IO, BinaryIO


def digest_of(data: bytes) -> str:
    """The one place SHA-256 is computed for storage keys — every put()
    and every independent-credential fixity check (FR-ING-07) calls this,
    never `hashlib.sha256` inline, so there is exactly one digest
    algorithm in the system to ever change."""
    return hashlib.sha256(data).hexdigest()


def key_for(digest: str) -> str:
    """`sha256/{d[0:2]}/{d[2:4]}/{digest}` — see ADR-004."""
    return f"sha256/{digest[0:2]}/{digest[2:4]}/{digest}"


def hash_stream_to_spooled_tempfile(
    stream: IO[bytes], *, chunk_size: int = 1 << 20, max_size_in_memory: int = 10 * 1024 * 1024
) -> tuple[str, tempfile.SpooledTemporaryFile]:
    """FR-ING-02 — the one place a large upload's SHA-256 is computed
    without ever holding the whole object in memory: read `stream` in
    bounded chunks, updating the digest incrementally, while spooling the
    same bytes into a `SpooledTemporaryFile` (kept in RAM only up to
    `max_size_in_memory`, spilled to disk beyond that — a 500-page PDF
    lands on disk, not in the process's heap). Returns the hex digest and
    the spooled file, positioned at 0 and ready for a driver's
    `_write_new_object` to stream onward. Caller owns closing it.
    """
    # Not a `with` block (ruff SIM115) deliberately — the spooled file
    # must outlive this function; it's returned open, and callers close
    # it (documented above).
    spooled = tempfile.SpooledTemporaryFile(max_size=max_size_in_memory)  # noqa: SIM115
    hasher = hashlib.sha256()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
        spooled.write(chunk)
    spooled.seek(0)
    return hasher.hexdigest(), spooled


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

    @abc.abstractmethod
    def open_stream(self, key: str) -> BinaryIO:
        """Open the object at `key` for sequential streaming read — the
        FR-ING-02/FR-ING-07 way to read a possibly-huge stored original
        (a 500-page PDF) without `get()`'s full-buffer-in-memory cost.
        Raises `KeyError` if absent. Caller closes it (a plain file-like,
        not necessarily a context manager depending on the driver — wrap
        in `contextlib.closing` if in doubt)."""

    def sign_get(self, key: str, ttl_seconds: int) -> str:
        """FR-REV-01/FR-SEC-08 (Phase 3, P3-04) — a short-TTL,
        access-controlled URL for retrieving `key`, never the object's
        bytes or an unsigned path. Default implementation: an
        application-level HMAC token (`landstorage.signing`) — good
        enough for the `local_fs` dev/test driver and any driver that
        doesn't have its own presigning mechanism. `S3ObjectStore`
        overrides this with a real provider-native presigned URL.
        """
        from landstorage.signing import sign

        signature, expires_at = sign(key, ttl_seconds)
        return f"/objects/{key}?exp={expires_at}&sig={signature}"

    @abc.abstractmethod
    def _write_new_object(self, key: str, fileobj: IO[bytes]) -> None:
        """Write `fileobj` (positioned at 0, containing exactly the bytes
        whose digest is embedded in `key` — already verified by
        `put_stream`) to the store, streaming rather than reading it
        fully into memory first. Not called directly by application
        code — `put_stream` is the public entrypoint; this is the one
        driver-specific primitive it needs."""

    def put_stream(self, stream: IO[bytes], *, chunk_size: int = 1 << 20) -> PutResult:
        """FR-ING-02/FR-ING-04 — the streaming counterpart to `put()`.
        Hashes `stream` incrementally (`hash_stream_to_spooled_tempfile`)
        and stores it at its content-addressed key, deduping exactly like
        `put()` (`created=False` on a digest match, no additional write) —
        the only difference is that neither this call nor its dedupe check
        ever holds the whole object in memory, which is what makes it safe
        to call on a 500-page scanned PDF.
        """
        digest, spooled = hash_stream_to_spooled_tempfile(stream, chunk_size=chunk_size)
        try:
            key = key_for(digest)
            if self.exists(key):
                if not self._streamed_content_matches(key, spooled):
                    raise ObjectAlreadyExistsWithDifferentContent(key)
                return PutResult(key=key, digest=digest, created=False)
            spooled.seek(0)
            self._write_new_object(key, spooled)
            return PutResult(key=key, digest=digest, created=True)
        finally:
            spooled.close()

    def _streamed_content_matches(self, key: str, spooled: IO[bytes], *, chunk_size: int = 1 << 20) -> bool:
        """Chunk-wise equality check between the already-stored object at
        `key` and `spooled` — used only on the rare digest-collision path
        `put()`/`put_stream` guard against (ADR-004); never loads either
        side fully into memory, so this check doesn't reintroduce the cost
        `put_stream` exists to avoid."""
        spooled.seek(0)
        existing = self.open_stream(key)
        try:
            while True:
                a = existing.read(chunk_size)
                b = spooled.read(chunk_size)
                if a != b:
                    return False
                if not a:
                    return True
        finally:
            existing.close()

    # There is deliberately no delete() or overwrite() on this port —
    # FR-ING-02 originals are immutable and never deleted by the pipeline.
    # A retention/erasure path (FR-SEC-06, P1) is a distinct, audited
    # operation and does not belong on the general-purpose port every
    # service imports.
