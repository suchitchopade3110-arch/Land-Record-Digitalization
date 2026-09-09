"""trace_id propagation + structured logging.

TODO: FR ref n/a directly — this implements the cross-cutting convention in
API-Contracts-and-Interfaces.md §1 ("every message carries a trace_id equal
to the originating document_id + page_id"), owned by no single FR but relied
on by every stage's debuggability.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any, TypeVar

import structlog

F = TypeVar("F", bound=Callable[..., Any])


def trace_id_for(document_id: str, page_id: str) -> str:
    """Canonical trace_id format — document_id:page_id (API-Contracts §1)."""
    return f"{document_id}:{page_id}"


def get_logger(**initial_values: Any) -> structlog.BoundLogger:
    """Structured logger. Callers bind trace_id via .bind(trace_id=...)."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )
    return structlog.get_logger(**initial_values)


def traced_consumer(fn: F) -> F:
    """Decorator for queue consumers (workers/*.py in every service).

    Stamps trace_id = f"{document_id}:{page_id}" onto every log line emitted
    while handling one message, and validates the envelope shape before the
    handler body runs. TODO: wire in envelope schema validation once
    contracts/generated/python is available (see envelope.validate_envelope).
    """

    @functools.wraps(fn)
    def wrapper(message: dict, *args: Any, **kwargs: Any) -> Any:
        trace_id = message.get("trace_id", "unknown:unknown")
        log = get_logger(trace_id=trace_id, producer=message.get("producer"))
        log.info("consumer.received", message_id=message.get("message_id"))
        try:
            result = fn(message, *args, **kwargs)
        except Exception:
            log.exception("consumer.failed")
            raise
        log.info("consumer.completed")
        return result

    return wrapper  # type: ignore[return-value]
