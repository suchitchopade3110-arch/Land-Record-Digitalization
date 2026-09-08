"""Alembic environment. TODO: point sqlalchemy.url at DATABASE_URL and wire
in contracts_generated / services/backend's SQLAlchemy metadata once models
exist. Single migration history across the monorepo (Skeleton-Prompt.md §1) —
schema changes require the sign-off CODEOWNERS routes."""
from logging.config import fileConfig

from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # TODO: import from backend's SQLAlchemy Base once defined


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    raise NotImplementedError("TODO: wire a real Engine once services/backend defines models")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
