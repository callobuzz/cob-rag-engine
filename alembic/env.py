"""Alembic async migration environment."""

import asyncio
import os
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config

# The migrations always create the Content Portal tables, but db.models only registers their
# models when the portal is enabled. Register them unconditionally here so autogenerate and
# `alembic check` never propose dropping them on an engine with the portal switched off.
import callobuzz_rag.content_portal.models  # noqa: F401
from callobuzz_rag.db.base import Base
from callobuzz_rag.db.models import *  # noqa: F403 — register all models

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config

# Inject DB connection params from env vars (set by docker-compose or .env)
config.set_section_option(config.config_ini_section, "DB_USER", os.getenv("RAG_DB_USER", "rag"))
config.set_section_option(
    config.config_ini_section, "DB_PASSWORD", os.getenv("RAG_DB_PASSWORD", "rag_password")
)
config.set_section_option(
    config.config_ini_section, "DB_HOST", os.getenv("RAG_DB_HOST", "localhost")
)
config.set_section_option(config.config_ini_section, "DB_PORT", os.getenv("RAG_DB_PORT", "5433"))
config.set_section_option(
    config.config_ini_section, "DB_NAME", os.getenv("RAG_DB_NAME", "rag_kernel")
)

if config.config_file_name is not None:
    # Keep the loggers that already exist: migrations run inside a process that has its own
    # (the integration tests) would otherwise silence every `callobuzz_rag` logger for good.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: "Connection") -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
