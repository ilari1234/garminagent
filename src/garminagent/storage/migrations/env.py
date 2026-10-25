"""Alembic environment. Online migrations only; the URL comes from `migrate.alembic_config`
or, when running the `alembic` CLI during development, from the app settings."""

from alembic import context
from sqlalchemy import create_engine, pool

from garminagent.config import get_settings
from garminagent.storage.db import warn_if_insecure
from garminagent.storage.models import Base

config = context.config


def _url() -> str:
    url = config.attributes.get("url")
    return url if isinstance(url, str) else get_settings().migration_database_url.get_secret_value()


def run_migrations_online() -> None:
    url = _url()
    warn_if_insecure(url)
    engine = create_engine(url, poolclass=pool.NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("offline (SQL script) migrations are not supported")
run_migrations_online()
