"""FR-PUB-08, ground rule 1 — "key material for signing roots is never
loadable by application code." A signing key that the application process
can read and export is not a mechanism against the threat model this band
exists for (an adversary who holds this application's own DB/process
credentials); it's a courtesy.

`SignOnlyKeyHandle` is the interface: `sign(payload) -> signature`, and
nothing else. No `export_key()`, no `get_private_key()` — the interface
itself has no such method for a caller to be tempted to add later. A real
deployment backs this with an actual KMS/HSM (the key is generated inside
the KMS and never leaves it; the app only ever calls a network RPC that
returns a signature). `LocalSignOnlyKMSStub` is the P0 simulation named in
the build prompt: the key lives in this process (there is no real KMS
available to this sandbox), but it is held in a module-private closure a
caller cannot reach through the public interface — the *interface*
enforces no-export even though the *deployment* does not yet.

Do not use `LocalSignOnlyKMSStub` past a pilot. State this in any
document that describes what P0 shipped, as PHASE4.md does.
"""
from __future__ import annotations

import abc
import hashlib
import hmac
import os
import uuid


class SignOnlyKeyHandle(abc.ABC):
    """A handle that can sign, and can prove which key id it is, and can
    do nothing else. This is the entire no-export contract — enforced by
    what methods exist, not by a comment asking a caller not to call one
    that isn't here."""

    @property
    @abc.abstractmethod
    def key_id(self) -> str: ...

    @abc.abstractmethod
    def sign(self, payload: bytes) -> str: ...


class LocalSignOnlyKMSStub(SignOnlyKeyHandle):
    """P0 simulation of a KMS/HSM sign-only key. The raw key bytes are
    held in a closure captured at construction and never assigned to any
    attribute this class exposes — `vars(handle)`/`handle.__dict__`
    inspection does not recover them, and there is no method that returns
    them. This is what "the interface enforces no-export" means in code;
    it is still, honestly, not hardware isolation, and a determined
    process-memory inspection of this same Python process could recover
    the key — a real KMS/HSM's isolation is a *different address space*
    (or a different piece of silicon) than the caller, which this stub
    cannot simulate. Never present this class as the real thing.
    """

    def __init__(self, *, key_id: str | None = None, seed_env_var: str = "ANCHOR_KMS_KEY_SEED"):
        self._key_id = key_id or f"local-kms-stub/{uuid.uuid4()}"
        seed = os.environ.get(seed_env_var)
        if not seed:
            raise RuntimeError(
                f"{seed_env_var} is not set — a real deployment provisions this key inside an actual "
                "KMS/HSM at setup time; this P0 stub still refuses to fabricate one from a fixed default, "
                "for the same reason landaudit.valuehash.EnvHmacKeyProvider refuses to."
            )
        _key_bytes = seed.encode()  # captured by the closures below; never stored on `self`

        def _sign(payload: bytes) -> str:
            return hmac.new(_key_bytes, payload, hashlib.sha256).hexdigest()

        self._sign_fn = _sign

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> str:
        return self._sign_fn(payload)


def verify_signature(handle: SignOnlyKeyHandle, payload: bytes, signature: str) -> bool:
    """A sign-only handle can also verify its own signatures (this is not
    an export — verification needs no secret material beyond what
    `sign()` already exposes through its output), which is what a
    verifier that does NOT hold the key still needs: recomputing
    `handle.sign(payload)` and comparing is the only verification path a
    caller without key access has, and it's exactly what this helper
    does, so `verify-chain` never has to touch key material at all."""
    return hmac.compare_digest(handle.sign(payload), signature)
