"""FR-REV-01/FR-SEC-08 (Phase 3, P3-04) — short-TTL, access-controlled
download URLs for crop delivery. This is the generic HMAC-signed-token
fallback every driver gets for free via `ObjectStorePort.sign_get`
(`landstorage/port.py`); `S3ObjectStore` overrides it with a real
provider-native presigned URL (`landstorage/drivers/s3.py`) since S3
already has a stronger mechanism than an application-level HMAC.

Never dereferenceable from a log entry alone (the architecture's own
wording, §18): the token embeds an expiry and a signature over
`key + expiry`, so a copy of the token without the shared secret is
useless, and nothing about the token itself reveals the secret or the
object's content.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time


def _secret() -> bytes:
    # A real deployment sets this; the fallback here is a fixed dev value
    # (same posture as landstorage.drivers.local_fs being the dev/test
    # default driver) — never used for a real credential.
    return os.environ.get("OBJECT_STORE_SIGNING_SECRET", "dev-only-insecure-signing-secret").encode()


def sign(key: str, ttl_seconds: int) -> tuple[str, int]:
    """Returns (signature, expires_at_unix). `key + expires_at` is the
    signed material — a token can't be replayed past its own expiry, and
    can't be forged for a different key or a longer TTL without the
    secret."""
    expires_at = int(time.time()) + ttl_seconds
    material = f"{key}:{expires_at}".encode()
    signature = hmac.new(_secret(), material, hashlib.sha256).hexdigest()
    return signature, expires_at


def verify(key: str, expires_at: int, signature: str) -> bool:
    if int(time.time()) > expires_at:
        return False
    material = f"{key}:{expires_at}".encode()
    expected = hmac.new(_secret(), material, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
