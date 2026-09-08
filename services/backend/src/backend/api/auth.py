"""RBAC — TODO: FR-SEC-01. Three hard-coded roles at P0 (operator, verifier,
supervisor/auditor/administrator per Architecture §18), least privilege,
no implicit escalation. Real identity provider is P1."""
from enum import Enum


class Role(str, Enum):
    OPERATOR = "operator"
    VERIFIER = "verifier"
    ADMINISTRATOR = "administrator"


def require_role(*allowed: Role):
    """TODO: FR-SEC-01 — dependency-injectable role check for FastAPI routes."""
    def _dependency():
        raise NotImplementedError("TODO: FR-SEC-01 — wire real auth/identity")
    return _dependency
