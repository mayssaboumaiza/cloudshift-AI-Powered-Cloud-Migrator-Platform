import logging

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from configuration.settings import db_settings

Base = declarative_base()

# -------------------------------
# Async engine
# -------------------------------
engine = create_async_engine(
    db_settings.POSTGRES_URI,
    echo=db_settings.ECHO,
    pool_size=db_settings.POOL_SIZE,
    max_overflow=db_settings.MAX_OVERFLOW,
    pool_timeout=db_settings.POOL_TIMEOUT,
    pool_recycle=db_settings.POOL_RECYCLE,
    future=True,
)

# -------------------------------
# Async session maker
# -------------------------------
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine, expire_on_commit=False, class_=AsyncSession
)


# -------------------------------
# Dependency for FastAPI
# -------------------------------
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async session generator for dependency injection."""
    async with async_session() as session:
        yield session


# -------------------------------
# Optional: startup helper
# -------------------------------
async def init_db():
    """
    Initialize database tables at startup.

    Strategy:
    - Production / Staging: run `alembic upgrade head` before starting the server.
      The `create_all` below is skipped when ALEMBIC_MANAGED=true.
    - Development / CI: `create_all` is used as a fallback to bootstrap tables
      without requiring Alembic to be run separately.

    Tables managed:
      Business Logic  : migrations, dependency_nodes, dependency_edges
      LangGraph       : pipeline_state
    """
    import os

    # Register all models so SQLAlchemy / Alembic can detect the schema.
    from data.models.migration_model import Migration  # noqa: F401
    from data.models.dependency_model import DependencyNode, DependencyEdge  # noqa: F401
    from data.models.checkpoint_model import PipelineState  # noqa: F401
    from data.models.secret_model import MigrationSecret, SecretAuditLog  # noqa: F401
    from data.models.runner_job_model import RunnerJob, DeploymentLog  # noqa: F401
    from data.models.user_model import User  # noqa: F401

    alembic_managed = os.getenv("ALEMBIC_MANAGED", "false").lower() == "true"

    if alembic_managed:
        # In managed environments, Alembic handles schema — just verify connectivity.
        logging.info("ALEMBIC_MANAGED=true — skipping create_all (Alembic manages schema)")
        async with engine.begin() as conn:
            await conn.run_sync(lambda _: None)  # connectivity check
        logging.info("Database connectivity OK")
    else:
        # Development fallback: create missing tables without dropping existing ones.
        # Safe to run repeatedly (create_all is idempotent).
        logging.info("Initializing database tables (dev mode — create_all)...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logging.info("Database ready (create_all). Use 'alembic upgrade head' in production.")


# -------------------------------
# Log database connection (mask password)
# -------------------------------
masked_uri = db_settings.POSTGRES_URI.replace(db_settings.DB_PASSWORD, "***")
logging.info(f"Database engine created: {masked_uri}")
