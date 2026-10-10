"""Alembic environment for the canonical CoachAI PostgreSQL schema."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from database import metadata as target_metadata


config = context.config

if config.config_file_name is not None:
    # Preserve application and standalone-worker loggers when migrations run
    # inside a longer-lived process (tests, maintenance tooling, or a future
    # deployment runner). Otherwise fileConfig disables unrelated loggers and
    # can silently hide operational failure signals.
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _configured_url() -> str:
    """Return an explicit migration URL without ever falling back to SQLite."""

    environment_url = os.environ.get("DATABASE_URL", "").strip()
    configured_url = config.get_main_option("sqlalchemy.url", "").strip()
    url = environment_url or configured_url
    if not url or url == "driver://required-via-DATABASE_URL":
        raise RuntimeError(
            "DATABASE_URL is required for migrations. Refusing to select an "
            "implicit database."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_configured_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied_connection = config.attributes.get("connection")
    if supplied_connection is not None:
        context.configure(
            connection=supplied_connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _configured_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
