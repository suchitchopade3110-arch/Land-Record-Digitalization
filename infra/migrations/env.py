"""Alembic environment. Single migration history across the monorepo
(CLAUDE.md) — `target_metadata` is a list combining every package's own
declarative Base, so `alembic revision --autogenerate` (if ever used
instead of hand-authored DDL) sees the whole shared schema at once.
Schema changes here require the sign-off `infra/CODEOWNERS` routes.
"""
import os
from logging.config import fileConfig

from alembic import context

# Import every package's Base metadata that contributes tables to the
# shared Postgres schema. Order doesn't matter for autogenerate; it does
# matter for readability of `alembic history`.
from backend.models.base import Base as BackendBase
from landaudit.models import AuditBase
from landenvelope.models import EnvelopeBase
from landoutbox.models import OutboxBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)

target_metadata = [EnvelopeBase.metadata, OutboxBase.metadata, AuditBase.metadata, BackendBase.metadata]


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import engine_from_config, pool

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
