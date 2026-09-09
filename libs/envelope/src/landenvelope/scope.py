"""Guards the "never resolve current model/config mid-pipeline" rule
(API-Contracts-and-Interfaces.md §7 rule 1; CLAUDE.md invariant 3).

Every worker that handles a message carrying a pinned `WorkEnvelope` enters
a scope with it before calling any downstream logic. If that logic then
tries to ask "what's the currently active model/config" (a live registry
lookup — `GET /models/{module}/active` or `GET /config/{scope}/{key}`
resolved to "the current one" rather than a specific version), the call
raises instead of silently returning a value that might differ from what
the pinned envelope says. This is what makes "someone reached for the live
registry inside a pinned stage by mistake" a loud failure instead of a
replay-nondeterminism bug discovered months later.
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar

_pinned_envelope_id: ContextVar[str | None] = ContextVar("_pinned_envelope_id", default=None)


class LiveResolutionInsidePinnedScope(RuntimeError):
    """Raised when code asks to resolve "the active model/config" while a
    WorkEnvelope is already pinned and in scope. The pinned envelope's
    values must be read instead (`landenvelope.pin.read`)."""


@contextlib.contextmanager
def pinned_scope(envelope_id: str):
    """Enter a scope for the duration of handling one message under
    `envelope_id`. Reentrant-safe for the common case (a handler that pins
    once at triage and then calls its own downstream helpers) but not
    nestable under a *different* envelope_id — a worker should never be
    mid-pipeline for two envelopes on one call stack."""
    token = _pinned_envelope_id.set(envelope_id)
    try:
        yield
    finally:
        _pinned_envelope_id.reset(token)


def current_pinned_envelope_id() -> str | None:
    return _pinned_envelope_id.get()


def guard_resolve_active(what: str) -> None:
    """Call this at the top of any "get current active X" function
    (a model-registry client, a config client's non-pinned path). Raises
    if a WorkEnvelope is already pinned for the message being handled.

    `what` is a short description for the error message, e.g.
    "model_versions.hwr" or "config_version" — named per call site so the
    exception says exactly what someone tried to re-resolve.
    """
    envelope_id = current_pinned_envelope_id()
    if envelope_id is not None:
        raise LiveResolutionInsidePinnedScope(
            f"refused to resolve 'active {what}' — WorkEnvelope {envelope_id} is already "
            "pinned for this message. Read the pinned value instead of the live registry "
            "(API-Contracts-and-Interfaces.md §7 rule 1)."
        )
