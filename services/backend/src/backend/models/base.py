"""Declarative Base + engine/session factory for services/backend's own
domain tables (everything in `entities.py`). `landenvelope.WorkEnvelope`
and `landoutbox.OutboxMessage` live in their own packages' Base registries
(they're shared mechanisms, not backend-specific domain concepts) but are
created in the *same* Postgres schema by the same migration history — see
`infra/migrations/versions/0002_core_schema.py`.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def engine_from_env():
    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://dev:dev@localhost:5432/landrecords")
    return create_engine(url, future=True)


def session_factory(engine=None) -> sessionmaker:
    return sessionmaker(bind=engine or engine_from_env(), expire_on_commit=False, future=True)
