"""
app.py - FastAPI entry point for Cloud Migrator.

Clean Architecture: API -> Service -> Data layers.
LangGraph agents are orchestrated by the Service layer.
"""
import logging
import warnings
from contextlib import asynccontextmanager

# Suppress LangGraph pending deprecation warning — allowed_objects API not yet
# available in the installed version; will be addressed on next LangGraph upgrade.
try:
    from langchain_core._api import LangChainPendingDeprecationWarning
    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except ImportError:
    warnings.filterwarnings("ignore", message=".*allowed_objects.*")
# Suppress HuggingFace FutureWarning — TRANSFORMERS_CACHE removed from our env vars.
warnings.filterwarnings("ignore", message=".*TRANSFORMERS_CACHE.*")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.middlewares.request_id_middleware import RequestIDMiddleware
from api.middlewares.logging_middleware import LoggingMiddleware
from api.middlewares.error_handling_middleware import ErrorHandlerMiddleware
from api.routers.v1 import migration_router
from api.routers.v1.credentials_router import router as credentials_router
from api.routers.v1.dependency_router import router as dependency_router
from api.routers.v1.github_router import router as github_router
from api.routers.v1.sse_router import router as sse_router
from api.routers.v1.graphrag_router import router as graphrag_router
from api.routers.v1.secrets_router import router as secrets_router, secrets_migration_router
from api.routers.v1.runner_router import router as runner_router, runner_migration_router
from api.routers.v1.rag_router import router as rag_router
from api.routers.v1.infra_health_router import router as infra_health_router
from api.routers.v1.alerts_router import router as alerts_router
from api.routers.v1.auth_router import router as auth_router
from api.routers.v1.audit_router import router as audit_router
from configuration.database import init_db, engine
from configuration.logging_setup import setup_logging
from configuration.settings import settings

logger = logging.getLogger("App")

from api.rate_limiter import limiter, SLOWAPI_AVAILABLE, RateLimitExceeded, _rate_limit_exceeded_handler


async def _auto_seed_rag() -> None:
    """Seed terraform_resources on startup if the table is empty."""
    import asyncio
    import os
    try:
        from rag.rag_config import get_pg_connection
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM terraform_resources")
                count = cur.fetchone()[0]

        if count == 0:
            logger.info("terraform_resources is empty — starting RAG corpus seeding...")
            github_token = os.environ.get("GITHUB_TOKEN", "")
            loop = asyncio.get_event_loop()
            from rag.corpus_builder import RAGBuilder
            builder = RAGBuilder(github_token=github_token)
            total = await loop.run_in_executor(None, builder.build_all)
            logger.info("RAG auto-seed complete — %d resources indexed", total)
            # Immediately patch semantic tags after a fresh seed
            from api.routers.v1.rag_router import _run_re_embed
            await loop.run_in_executor(None, _run_re_embed, None)
            logger.info("RAG semantic tag re-embed complete")
        else:
            logger.info("terraform_resources already populated (%d rows) — applying semantic tag patches...", count)
            # Always re-apply semantic tags on startup (cheap, ~60 records, <5s)
            loop = asyncio.get_event_loop()
            from api.routers.v1.rag_router import _run_re_embed
            result = await loop.run_in_executor(None, _run_re_embed, None)
            logger.info("RAG semantic tag re-embed complete: %s", result)

        # Always ensure canonical patterns are seeded (idempotent upsert, ~14 patterns, <3s)
        try:
            loop = asyncio.get_event_loop()
            from rag.seed_canonical_patterns import seed_patterns
            n = await loop.run_in_executor(None, seed_patterns, False)
            logger.info("Canonical patterns seeded: %d upserted", n)
        except Exception as patterns_exc:
            logger.warning("Canonical patterns seed skipped: %s", patterns_exc)

        # Seed cloud ontology (idempotent, ~45 entries, <2s)
        try:
            loop = asyncio.get_event_loop()
            from rag.cloud_ontology import seed_ontology
            n = await loop.run_in_executor(None, seed_ontology, False)
            logger.info("Cloud ontology seeded: %d entries upserted", n)
        except Exception as ontology_exc:
            logger.warning("Cloud ontology seed skipped: %s", ontology_exc)

        # Dynamic tag enrichment (LLM) is triggered per-migration after stack analysis,
        # scoped to only the detected resource IDs — not run globally at startup.

    except Exception as exc:
        logger.error(
            "RAG auto-seed failed — semantic search will be degraded: %s",
            exc,
            exc_info=True,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    import asyncio
    setup_logging()
    await init_db()
    from pipeline.pipeline_graph import get_compiled_graph
    get_compiled_graph()
    # Run RAG seeding in background so the server becomes ready immediately
    asyncio.ensure_future(_auto_seed_rag())
    # Scan for interrupted deployments (browser closed during terraform apply)
    try:
        from executor.cleanup import startup_cleanup_scan
        asyncio.ensure_future(startup_cleanup_scan())
    except Exception as _cleanup_exc:
        logger.warning("Cleanup scan skipped: %s", _cleanup_exc)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.TITLE,
        version=settings.VERSION,
        description=settings.DESCRIPTION,
        docs_url=settings.DOCS_URL,
        openapi_url=settings.OPENAPI_URL,
        lifespan=lifespan,
    )

    # ── Rate limiting state (SlowAPI requires app.state.limiter) ──────────────
    if SLOWAPI_AVAILABLE:
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── Prometheus metrics (/metrics endpoint) ─────────────────────────────────
    try:
        from prometheus_fastapi_instrumentator import Instrumentator
        Instrumentator().instrument(app).expose(app, endpoint="/metrics", tags=["OBSERVABILITY"])
        logger.info("Prometheus /metrics endpoint registered")
    except ImportError:
        logger.warning("prometheus-fastapi-instrumentator not installed — /metrics unavailable")

    # ── Middlewares (order matters: outermost first) ───────────────────────────
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(
        migration_router.router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        github_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        credentials_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        dependency_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(sse_router, prefix=f"{settings.API_PREFIX}/v1")
    app.include_router(
        graphrag_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        secrets_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        secrets_migration_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        runner_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        runner_migration_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        rag_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        infra_health_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        alerts_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        auth_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )
    app.include_router(
        audit_router,
        prefix=f"{settings.API_PREFIX}/v1",
    )

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/health", tags=["HEALTH"])
    async def health():
        """Health check endpoint."""
        db_status = "unknown"
        db_detail = None
        try:
            async with engine.begin() as conn:
                try:
                    result = await conn.execute(
                        text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'")
                    )
                    row = result.fetchone()
                    table_count = row[0] if row else 0
                    if table_count > 0:
                        db_status = "ok"
                        db_detail = f"{table_count} tables ready"
                    else:
                        db_status = "initializing"
                        db_detail = "Database connected, waiting for tables initialization"
                except Exception as query_err:
                    db_status = "initializing"
                    db_detail = "Database connected, initializing tables"
                    logger.debug(f"Health check query failed (non-critical): {query_err}")
        except Exception as exc:
            db_status = "error"
            db_detail = str(exc)
            logger.warning(f"Health check error: {exc}")

        overall_status = "degraded" if db_status == "error" else "ok"
        http_status = 503 if db_status == "error" else 200
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=http_status,
            content={
                "status": overall_status,
                "version": settings.VERSION,
                "database": {"status": db_status, "detail": db_detail},
                "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            },
        )

    return app


app = create_app()
