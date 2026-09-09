"""FR-SEC-08 — keyed value hashing for `AuditEntry.value_hash`.

A bare `hashlib.sha256(value)` is not sufficient here. Owner names, share
fractions, survey numbers, and khata numbers all live in small,
enumerable domains — an attacker with read access to the audit log store
(or even just to a table dump) can precompute SHA-256 over every name in
a voters'-roll-sized dictionary and recover which hash belongs to which
name, with no need to ever see the plaintext. A *keyed* hash (HMAC) closes
that: recovering a value from `hmac_value_hash(value)` requires the key,
which this module deliberately does not expose to whatever holds the log.

Key custody (state this plainly, not left implicit): the HMAC key lives in
a `HmacKeyProvider` the audit-log *reader* role never receives — in this
P0, `EnvHmacKeyProvider` reads it from an environment variable
(`AUDIT_HMAC_KEY`) that must be present in the *writer's* process
environment (`services/backend`'s API/worker containers) and absent from
whatever reads `audit_entry` for display (an "auditor" role process, a
BI/reporting job). See PHASE4.md's "HMAC key custody" section for exactly
who/what is allowed to hold this key in a real deployment: a secrets
service (e.g. KMS-backed) the audit-log-reader IAM role has no grant to,
mirroring the same "reachable by the writer, not by the reader" shape
`Witness` (`witness.py`) uses for anchoring key material.
"""
from __future__ import annotations

import abc
import hashlib
import hmac
import os


class HmacKeyProvider(abc.ABC):
    """The only thing `hmac_value_hash` is allowed to ask for a key from.
    A real implementation must be reachable by the process that *writes*
    audit entries and not by whatever *reads* them for display — see this
    module's docstring."""

    @abc.abstractmethod
    def get_key(self, *, purpose: str = "audit_value_hash") -> bytes: ...


class EnvHmacKeyProvider(HmacKeyProvider):
    """P0 stand-in: reads the key from an environment variable. The
    *mechanism* (a key the hasher needs but never persists or logs) is
    real; the *custody boundary* — this env var must exist in the writer
    process's environment and must not exist in the reader's — is an
    operational deployment property this class cannot itself enforce. A
    real deployment replaces this with a client for an actual secrets
    service (e.g. a KMS-backed secret, fetched at process start, never
    written to disk) that grants access to the writer role and refuses it
    to the auditor-log-reader role."""

    def __init__(self, env_var: str = "AUDIT_HMAC_KEY"):
        self._env_var = env_var

    def get_key(self, *, purpose: str = "audit_value_hash") -> bytes:
        # `purpose` namespaces a real KMS key path; this env-var stand-in
        # ignores it and always returns the one configured key.
        value = os.environ.get(self._env_var)
        if not value:
            # Never silently fall back to a fixed dev key here the way
            # landstorage.signing does for crop URLs — a fixed key would
            # make every deployment's audit log dictionary-attackable with
            # the same precomputed table. Fail loudly instead.
            raise RuntimeError(
                f"{self._env_var} is not set — a real HMAC key is required to hash audit values (FR-SEC-08); "
                "there is no safe default here, unlike landstorage's crop-URL signing secret"
            )
        return value.encode()


def hmac_value_hash(value: str, *, key_provider: HmacKeyProvider, purpose: str = "audit_value_hash") -> str:
    """The one function every P4 call site uses to turn a plaintext value
    into what `AuditEntry.value_hash` stores. Never call `hashlib.sha256`
    directly on a personal-data value for this purpose — see this module's
    docstring for why a bare hash is not sufficient."""
    key = key_provider.get_key(purpose=purpose)
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
