"""P4-05/T4.f-shaped — every `Witness` implementation satisfies the same
contract. No real store or KMS needed: a tiny in-memory fake stands in for
`landstorage.ObjectStorePort` (the anchor store), same technique
`services/backend`'s own contract tests use for a real dependency they
don't want to spin up.
"""
from dataclasses import dataclass

import pytest

from landaudit.kms import LocalSignOnlyKMSStub
from landaudit.witness import SecondStoreWitness, TimestampAuthorityWitness, Witness, WitnessReceipt


@dataclass
class _FakePutResult:
    key: str


class _FakeAnchorStore:
    """Write-only, append-only fake — mirrors the real anchor store's
    posture (the application container only ever gets a write-only
    append credential to it, per PHASE4.md)."""

    def __init__(self):
        self.written: list[bytes] = []

    def put(self, data: bytes) -> _FakePutResult:
        self.written.append(data)
        return _FakePutResult(key=f"fake-key-{len(self.written)}")


@pytest.fixture(autouse=True)
def _kms_seed(monkeypatch):
    monkeypatch.setenv("ANCHOR_KMS_KEY_SEED", "test-only-seed-do-not-use-in-prod")


WITNESS_FACTORIES = {
    "second_store": lambda: SecondStoreWitness(_FakeAnchorStore()),
    "timestamp_authority_stub": lambda: TimestampAuthorityWitness(),
}


@pytest.mark.parametrize("name", sorted(WITNESS_FACTORIES))
def test_every_witness_implements_the_interface(name):
    assert isinstance(WITNESS_FACTORIES[name](), Witness)


@pytest.mark.parametrize("name", sorted(WITNESS_FACTORIES))
def test_every_witness_anchors_a_root_and_returns_a_receipt(name):
    witness = WITNESS_FACTORIES[name]()
    receipt = witness.anchor("deadbeef" * 8)

    assert isinstance(receipt, WitnessReceipt)
    assert receipt.root == "deadbeef" * 8
    assert receipt.witness_reference  # non-empty — points at something the witness can be asked about later
    assert receipt.anchored_at


@pytest.mark.parametrize("name", sorted(WITNESS_FACTORIES))
def test_every_witness_signs_when_given_a_signer_and_records_which_key(name):
    witness = WITNESS_FACTORIES[name]()
    signer = LocalSignOnlyKMSStub(key_id="anchor-key-1")

    receipt = witness.anchor("cafebabe" * 8, signer=signer)

    assert receipt.signature == signer.sign(b"cafebabe" * 8)
    assert receipt.kms_key_id == "anchor-key-1"


@pytest.mark.parametrize("name", sorted(WITNESS_FACTORIES))
def test_every_witness_omits_signature_when_no_signer_is_given(name):
    witness = WITNESS_FACTORIES[name]()
    receipt = witness.anchor("cafebabe" * 8)
    assert receipt.signature is None
    assert receipt.kms_key_id is None


def test_second_store_witness_never_reuses_the_applications_own_store_object():
    """Structural check standing in for the docker-compose credential
    separation (not runnable in this sandbox, see PHASE4.md): the witness
    only ever calls `.put()` on whatever store object it was constructed
    with — never imports or reaches for `landstorage.get_store()`
    itself, which would silently reintroduce the application's own
    primary-store credential into the anchoring path."""
    import inspect
    import textwrap
    import tokenize
    from io import StringIO

    source = textwrap.dedent(inspect.getsource(SecondStoreWitness))
    code_only = " ".join(
        tok.string
        for tok in tokenize.generate_tokens(StringIO(source).readline)
        if tok.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE)
    )
    assert "get_store" not in code_only
    assert "get_secondary_store" not in code_only
