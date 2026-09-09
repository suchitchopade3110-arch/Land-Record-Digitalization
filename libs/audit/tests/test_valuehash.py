"""FR-SEC-08 — proves the HMAC value hash is keyed (not a bare SHA-256),
i.e. resists exactly the dictionary attack a bare hash of a small-domain
value (an owner name, a survey number) would fall to."""
import hashlib

import pytest

from landaudit.valuehash import EnvHmacKeyProvider, hmac_value_hash


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("AUDIT_HMAC_KEY", "test-only-hmac-key-do-not-use-in-prod")


def test_hash_is_not_a_bare_sha256_of_the_value():
    """The whole point of P4-07: a bare SHA-256 is dictionary-attackable
    over a realistic name domain; the keyed hash must not equal it."""
    value = "Ram Prasad"
    keyed = hmac_value_hash(value, key_provider=EnvHmacKeyProvider())
    bare = hashlib.sha256(value.encode()).hexdigest()
    assert keyed != bare


def test_dictionary_attack_over_a_realistic_name_domain_does_not_recover_the_value():
    """T4.d's shape, exercised directly against the primitive: precompute
    a bare SHA-256 dictionary over a plausible name domain, and confirm
    none of those precomputed values equal the keyed hash actually
    stored — the attack that would work against a bare hash fails here."""
    real_value = "Suresh Kumar"
    stored = hmac_value_hash(real_value, key_provider=EnvHmacKeyProvider())

    candidate_names = [
        "Ram Prasad", "Suresh Kumar", "Anita Devi", "Mohammed Iqbal", "Lakshmi Bai",
        "Ganesh Rao", "Fatima Begum", "Vikram Singh", "Priya Sharma", "Abdul Rahman",
    ]
    dictionary = {hashlib.sha256(name.encode()).hexdigest(): name for name in candidate_names}
    assert stored not in dictionary  # even though "Suresh Kumar" IS in the dictionary


def test_same_value_same_key_same_purpose_is_deterministic():
    provider = EnvHmacKeyProvider()
    assert hmac_value_hash("Ram Prasad", key_provider=provider) == hmac_value_hash("Ram Prasad", key_provider=provider)


def test_different_keys_produce_different_hashes_for_the_same_value(monkeypatch):
    h1 = hmac_value_hash("Ram Prasad", key_provider=EnvHmacKeyProvider())
    monkeypatch.setenv("AUDIT_HMAC_KEY", "a-completely-different-key")
    h2 = hmac_value_hash("Ram Prasad", key_provider=EnvHmacKeyProvider())
    assert h1 != h2


def test_provider_refuses_to_fabricate_a_default_key(monkeypatch):
    monkeypatch.delenv("AUDIT_HMAC_KEY", raising=False)
    with pytest.raises(RuntimeError, match="AUDIT_HMAC_KEY"):
        hmac_value_hash("x", key_provider=EnvHmacKeyProvider())
