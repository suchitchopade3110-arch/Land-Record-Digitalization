"""Shared observability library — trace_id propagation, structured logging,
and queue envelope emission, used identically by all four services.

TODO: [DESIGN CHOICE] trace_id format is document_id:page_id, per
API-Contracts-and-Interfaces.md §1. Not a PRD-sourced requirement — agreed
by the team so any subsystem can be debugged from a single log query.
"""

from .tracing import trace_id_for, traced_consumer, get_logger
from .envelope import emit, validate_envelope
from .config_client import ConfigClient

__all__ = [
    "trace_id_for",
    "traced_consumer",
    "get_logger",
    "emit",
    "validate_envelope",
    "ConfigClient",
]
