"""Filesystem-backed driver — dev/test default and the shape any real
POSIX network filesystem mount would take. Immutability is enforced here
by refusing to overwrite an existing path (object-lock policy in prod
S3-compatible storage does the equivalent job for the `s3` driver)."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import IO, BinaryIO

from landstorage.port import (
    ObjectAlreadyExistsWithDifferentContent,
    ObjectStorePort,
    PutResult,
    digest_of,
    key_for,
)


class LocalFsObjectStore(ObjectStorePort):
    def __init__(self, root: str | Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._root / key

    def put(self, data: bytes) -> PutResult:
        digest = digest_of(data)
        key = key_for(digest)
        path = self._path(key)

        if path.exists():
            existing = path.read_bytes()
            if existing != data:
                raise ObjectAlreadyExistsWithDifferentContent(key)
            return PutResult(key=key, digest=digest, created=False)

        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory then atomically
        # rename, so a concurrent reader never observes a partial write
        # and a crash mid-write never leaves a corrupt object at `key`.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            # Immutability: refuse to clobber a real object that appeared
            # between our exists() check and now (a genuine race — two
            # writers with the same content is harmless, so re-check
            # content before erroring rather than failing on the race
            # itself).
            if path.exists():
                if path.read_bytes() != data:
                    raise ObjectAlreadyExistsWithDifferentContent(key)
                os.unlink(tmp_name)
                return PutResult(key=key, digest=digest, created=False)
            os.replace(tmp_name, path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return PutResult(key=key, digest=digest, created=True)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise KeyError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def verify_fixity(self, key: str) -> bool:
        data = self.get(key)
        expected_digest = key.rsplit("/", 1)[-1]
        return digest_of(data) == expected_digest

    def open_stream(self, key: str) -> BinaryIO:
        path = self._path(key)
        if not path.exists():
            raise KeyError(key)
        return path.open("rb")

    def _write_new_object(self, key: str, fileobj: IO[bytes]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Same atomic write-then-rename discipline as put(): stream into a
        # temp file in the same directory (never loading `fileobj` fully
        # into memory), then rename — a concurrent reader never sees a
        # partial write and a crash mid-copy never leaves a corrupt object
        # at `key`.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as dest:
                shutil.copyfileobj(fileobj, dest)
            if path.exists():
                # Lost a race with another writer of the same content —
                # harmless (ADR-004): the key IS the digest, so whatever
                # landed there already is byte-identical to what we were
                # about to write. Caller's exists() check already covers
                # the "different content" case via _streamed_content_matches.
                os.unlink(tmp_name)
                return
            os.replace(tmp_name, path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
