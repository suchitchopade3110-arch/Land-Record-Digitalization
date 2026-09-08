"""Reads config (unit tables, gazetteer closed sets) from Suchit's Config
Service at startup + on invalidation event. TODO: FR-CFG-02.
"""
from observability import ConfigClient


def _fetch_over_http(scope: str, key: str) -> dict:
    # TODO: call GET /config/{scope}/{key} on backend, per gateway/route_registry.yaml.
    raise NotImplementedError("TODO: wire HTTP call to Config Service")


config_client = ConfigClient(fetch=_fetch_over_http)
