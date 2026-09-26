"""Async-engine-compatible Alembic environment script.

The real Postgres DSN (`postgresql+asyncpg://...`) comes from application
`Settings` (env vars / `.env`), not from a hardcoded value in `alembic.ini` —
we override `sqlalchemy.url` at runtime below rather than relying on the
ini file for it.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from banner_copilot.config import get_settings
from banner_copilot.db import Base

# Imported for side effect only: registers all domain tables on
# Base.metadata so `target_metadata` below picks them up.
from banner_copilot.domain import models  # noqa: F401

# This is the Alembic Config object, which provides access to the values
# within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the ini's placeholder sqlalchemy.url with the real DSN from
# application Settings (env vars / .env).
config.set_main_option("sqlalchemy.url", get_settings().database_url)

# `Base.metadata` now includes Encounter/DraftNote/SummaryRequest/AuditEvent
# thanks to the `banner_copilot.domain.models` import above.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Build an async Engine from config and run migrations against it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live (async) DB connection."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
