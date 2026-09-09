"""The producer side of ADR-005. `write()` must be called inside the same
DB session/transaction as the domain row it accompanies, and the caller
must be the one who calls `session.commit()` — this module never commits,
so "wrote the domain row" and "queued the outbox entry" are guaranteed to
land in one transaction or not at all.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from landoutbox.models import OutboxMessage


def write(session: Session, *, queue: str, envelope: dict[str, Any]) -> OutboxMessage:
    """Enqueue `envelope` (the full shape from
    `observability.envelope.emit()`) for eventual delivery to `queue`.
    Does not publish and does not commit — call this alongside your
    domain write, inside the same `with session.begin():` block (or before
    a single `session.commit()`), so both succeed or fail together.
    """
    message = OutboxMessage(queue=queue, envelope=envelope)
    session.add(message)
    session.flush()
    return message
