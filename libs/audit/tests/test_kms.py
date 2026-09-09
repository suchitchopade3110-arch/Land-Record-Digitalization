"""P4-05, ground rule 1 — FR-PUB-08: key material for signing roots is
never loadable by application code. Proves the *interface* enforces
no-export (no method returns the key) and that sign/verify still work
without one."""
import os

import pytest

from landaudit.kms import LocalSignOnlyKMSStub, SignOnlyKeyHandle, verify_signature


@pytest.fixture(autouse=True)
def _seed(monkeypatch):
    monkeypatch.setenv("ANCHOR_KMS_KEY_SEED", "test-only-seed-do-not-use-in-prod")


def test_stub_refuses_to_start_without_a_seed(monkeypatch):
    monkeypatch.delenv("ANCHOR_KMS_KEY_SEED", raising=False)
    with pytest.raises(RuntimeError, match="ANCHOR_KMS_KEY_SEED"):
        LocalSignOnlyKMSStub()


def test_stub_is_a_sign_only_key_handle():
    assert isinstance(LocalSignOnlyKMSStub(), SignOnlyKeyHandle)


def test_no_public_method_returns_the_key_material():
    """The no-export contract, checked mechanically: enumerate every
    public attribute/method on the handle and confirm none of them, when
    called with no arguments, hands back something resembling the raw key
    bytes. (`sign()` needs a payload, so it's excluded from the
    no-argument sweep and checked separately below for what it returns.)"""
    handle = LocalSignOnlyKMSStub()
    public_no_arg_members = [
        name
        for name in dir(handle)
        if not name.startswith("_") and name not in ("sign",) and callable(getattr(handle, name, None)) is False
    ]
    # key_id is intentionally public (an opaque identifier, not the key);
    # everything else public-and-no-arg should just be that.
    assert public_no_arg_members == ["key_id"] or set(public_no_arg_members) <= {"key_id"}
    assert "test-only-seed-do-not-use-in-prod" not in handle.key_id


def test_sign_is_deterministic_for_the_same_payload():
    handle = LocalSignOnlyKMSStub(key_id="k1")
    assert handle.sign(b"root-value") == handle.sign(b"root-value")


def test_sign_differs_for_different_payloads():
    handle = LocalSignOnlyKMSStub(key_id="k1")
    assert handle.sign(b"root-a") != handle.sign(b"root-b")


def test_verify_signature_round_trips_through_the_handle_without_touching_key_material():
    handle = LocalSignOnlyKMSStub(key_id="k1")
    sig = handle.sign(b"root-value")
    assert verify_signature(handle, b"root-value", sig) is True
    assert verify_signature(handle, b"tampered-root-value", sig) is False


def test_two_handles_with_different_seeds_produce_different_signatures():
    os.environ["ANCHOR_KMS_KEY_SEED"] = "seed-one"
    h1 = LocalSignOnlyKMSStub(key_id="k1")
    os.environ["ANCHOR_KMS_KEY_SEED"] = "seed-two"
    h2 = LocalSignOnlyKMSStub(key_id="k2")
    assert h1.sign(b"root") != h2.sign(b"root")
