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

from landstorage.port import (
    ObjectAlreadyExistsWithDifferentContent,
    ObjectStorePort,
    PutResult,
    digest_of,
    key_for,
)


class S3ObjectStore(ObjectStorePort):
    def __init__(self, bucket: str, *, endpoint_url: str | None = None, client=None):
        try:
            import boto3
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "landstorage.drivers.s3 requires the 's3' extra: pip install 'landstorage[s3]'"
            ) from e
        self._bucket = bucket
        self._s3 = client or boto3.client("s3", endpoint_url=endpoint_url)

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
