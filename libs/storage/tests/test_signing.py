"""P3-04 — sign_get()'s HMAC token: verifies, expires, and can't be forged
for a different key or a longer TTL without the secret."""
import time

from landstorage.drivers.local_fs import LocalFsObjectStore
from landstorage.signing import sign, verify


def test_sign_get_produces_a_url_carrying_expiry_and_signature(tmp_path):
    store = LocalFsObjectStore(root=tmp_path)
    put_result = store.put(b"a crop's source page bytes")

    url = store.sign_get(put_result.key, ttl_seconds=60)

    assert put_result.key in url
    assert "sig=" in url
    assert "exp=" in url


def test_verify_accepts_its_own_signature():
    key = "sha256/ab/cd/deadbeef"
    signature, expires_at = sign(key, ttl_seconds=60)
    assert verify(key, expires_at, signature) is True


def test_verify_rejects_a_tampered_key():
    key = "sha256/ab/cd/deadbeef"
    signature, expires_at = sign(key, ttl_seconds=60)
    assert verify("sha256/ab/cd/somethingelse", expires_at, signature) is False


def test_verify_rejects_an_expired_token():
    key = "sha256/ab/cd/deadbeef"
    signature, expires_at = sign(key, ttl_seconds=1)
    time.sleep(2.5)  # comfortably past the 1s TTL, clear of second-boundary rounding
    assert verify(key, expires_at, signature) is False


def test_verify_rejects_an_extended_expiry_without_a_matching_signature():
    key = "sha256/ab/cd/deadbeef"
    signature, expires_at = sign(key, ttl_seconds=60)
    assert verify(key, expires_at + 3600, signature) is False
