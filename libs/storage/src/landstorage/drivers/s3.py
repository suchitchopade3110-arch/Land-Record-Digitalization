"""S3-compatible driver — MinIO in `infra/docker-compose.yml`, and prod
S3-compatible storage. `boto3` is imported lazily so `landstorage` doesn't
hard-depend on it; install with `pip install "landstorage[s3]"`.

Object-lock (WORM) at the bucket level is how FR-ING-02 immutability is
actually enforced in this driver — configure the bucket with Object Lock
enabled and a retention policy at provisioning time; this driver additionally
refuses an application-level overwrite of differing content at an existing
key, matching `local_fs`'s behavior, so the *application* never attempts
the clobber even before the bucket policy would refuse it.
"""
from __future__ import annotations

from typing import IO, BinaryIO

from landstorage.port import (
    ObjectAlreadyExistsWithDifferentContent,
    ObjectStorePort,
    PutResult,
    digest_of,
    key_for,
)


class S3ObjectStore(ObjectStorePort):
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client=None,
    ):
        """`access_key_id`/`secret_access_key` are optional — omit both to
        fall back to boto3's normal credential chain (env/profile/role).
        Pass them explicitly when this store must use a credential set
        genuinely independent of another store's (FR-ING-07's dual-copy
        requirement, `landstorage.distinct.assert_distinct_stores`) —
        two `S3ObjectStore`s pointed at the same bucket via the same
        ambient credentials would not satisfy that requirement even
        though nothing about this class itself would notice.
        """
        try:
            import boto3
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "landstorage.drivers.s3 requires the 's3' extra: pip install 'landstorage[s3]'"
            ) from e
        self._bucket = bucket
        self._s3 = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def _exists_and_get(self, key: str) -> bytes | None:
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        except self._s3.exceptions.NoSuchKey:
            return None
        except self._s3.exceptions.ClientError as e:  # pragma: no cover
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return resp["Body"].read()

    def put(self, data: bytes) -> PutResult:
        digest = digest_of(data)
        key = key_for(digest)

        existing = self._exists_and_get(key)
        if existing is not None:
            if existing != data:
                raise ObjectAlreadyExistsWithDifferentContent(key)
            return PutResult(key=key, digest=digest, created=False)

        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data)
        return PutResult(key=key, digest=digest, created=True)

    def get(self, key: str) -> bytes:
        data = self._exists_and_get(key)
        if data is None:
            raise KeyError(key)
        return data

    def exists(self, key: str) -> bool:
        return self._exists_and_get(key) is not None

    def verify_fixity(self, key: str) -> bool:
        data = self.get(key)
        expected_digest = key.rsplit("/", 1)[-1]
        return digest_of(data) == expected_digest

    def sign_get(self, key: str, ttl_seconds: int) -> str:
        # S3's own presigning is stronger than the generic HMAC fallback
        # (`ObjectStorePort.sign_get`) — it's verified by the object store
        # itself, not by this application re-checking a signature it
        # minted. FR-REV-01/FR-SEC-08 (Phase 3, P3-04).
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=ttl_seconds
        )

    def open_stream(self, key: str) -> BinaryIO:
        try:
            resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        except self._s3.exceptions.NoSuchKey as e:
            raise KeyError(key) from e
        return resp["Body"]  # botocore StreamingBody — .read(n) works like a plain file

    def _write_new_object(self, key: str, fileobj: IO[bytes]) -> None:
        # upload_fileobj streams in bounded parts (multipart past boto3's
        # default threshold) — never reads `fileobj` fully into memory
        # itself, which is the point of routing a 500-page PDF through
        # put_stream() rather than put().
        self._s3.upload_fileobj(fileobj, self._bucket, key)
