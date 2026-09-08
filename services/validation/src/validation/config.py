"""Reads validator tolerances from Suchit's Config Service. TODO: FR-VAL-07 (P1)."""
from observability import ConfigClient


def _fetch_over_http(scope: str, key: str) -> dict:
    raise NotImplementedError("TODO: wire HTTP call to Config Service")


config_client = ConfigClient(fetch=_fetch_over_http)
