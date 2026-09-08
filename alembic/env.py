"""
alembic/env.py — Alembic migration environment for Cloud Migrator.

Supports async SQLAlchemy engine (asyncpg driver).
Run migrations:
    alembic upgrade head                      # apply all pending migrations
    alembic revision --autogenerate -m "..."  # generate migration from model diff
"""
import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ── Make project root importable ──────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Import Base + all models (Alembic needs them to detect schema changes) ────
from configuration.database import Base  # noqa: E402
from configuration.settings import db_settings  # noqa: E402

# Registering models with Base.metadata (imports are side-effects)
from data.models.migration_model import Migration  # noqa: F401, E402
from data.models.dependency_model import (  # noqa: F401, E402
    DependencyNode, DependencyEdge,
)
from data.models.checkpoint_model import PipelineState, PipelineEvent  # noqa: F401, E402
from data.models.audit_log_model import AuditLog  # noqa: F401, E402
from data.models.rag_message_model import RagMessage  # noqa: F401, E402

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

# Override sqlalchemy.url from settings — never hardcode credentials in alembic.ini
config.set_main_option("sqlalchemy.url", db_settings.POSTGRES_URI)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Offline mode: generates SQL script without a live DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _ensure_wide_version_table(connection: Connection) -> None:
    """Pre-create alembic_version with a wide version_num column.

    Alembic defaults version_num to VARCHAR(32), but some revision ids here are
    33 chars (e.g. 002_consolidate_dependency_schema) and overflow the stamp.
    Alembic reuses an existing alembic_version table as-is, so creating/widening
    it here makes the longer ids fit. Idempotent. PostgreSQL only.
    """
    if connection.dialect.name != "postgresql":
        return
    from sqlalchemy import inspect as _inspect
    if _inspect(connection).has_table("alembic_version"):
        connection.exec_driver_sql(
            "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"
        )
    else:
        connection.exec_driver_sql(
            "CREATE TABLE alembic_version ("
            "version_num VARCHAR(255) NOT NULL, "
            "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        )


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Online mode with async engine (required for asyncpg driver)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # Widen alembic_version.version_num in its own committed transaction first,
    # so the long revision ids fit before Alembic stamps them.
    async with connectable.begin() as connection:
        await connection.run_sync(_ensure_wide_version_table)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
