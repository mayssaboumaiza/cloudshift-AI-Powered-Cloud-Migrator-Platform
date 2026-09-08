"""
rag_router.py — Endpoints for RAG corpus management.

Endpoints:
  POST /rag/seed      → seed terraform_resources table (background task)
  POST /rag/re-embed  → patch embed_text + recompute embeddings for specific resources
  GET  /rag/status    → count of indexed resources per provider
"""
import logging
import os

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse

from api.auth.api_key import require_api_key

router = APIRouter(prefix="/rag", tags=["RAG"])
logger = logging.getLogger("rag_router")


def _count_resources() -> dict:
    """Return counts per provider directly from DB."""
    try:
        from rag.rag_config import get_pg_connection
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT provider, COUNT(*) FROM terraform_resources GROUP BY provider"
                )
                rows = cur.fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception as exc:
        logger.warning("Could not count terraform_resources: %s", exc)
        return {}


def _run_seed(force: bool) -> None:
    """Background worker: build the RAG corpus."""
    try:
        github_token = os.environ.get("GITHUB_TOKEN", "")
        from rag.corpus_builder import RAGBuilder
        builder = RAGBuilder(github_token=github_token)
        total = builder.build_all(force=force)
        logger.info("RAG seed completed — %d resources indexed", total)
    except Exception as exc:
        logger.error("RAG seed failed: %s", exc, exc_info=True)


def _run_re_embed(resource_ids: list[str] | None) -> dict:
    """
    Recompute embed_text + embedding for specific resources (or all _SEMANTIC_TAGS keys).
    Does NOT re-fetch docs from GitHub — uses existing description/required_args in DB.
    Returns a summary dict.
    """
    from rag.rag_config import get_pg_connection, EMBEDDING_MODEL
    from core.embedding_model_loader import get_shared_embedder
    from rag.corpus_builder import RAGBuilder

    semantic_tags = RAGBuilder._SEMANTIC_TAGS

    # If no explicit list, patch every resource that has a semantic tag defined
    ids_to_patch = resource_ids if resource_ids else list(semantic_tags.keys())

    # Fetch current rows
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, description, required_args FROM terraform_resources WHERE id = ANY(%s)",
                (ids_to_patch,),
            )
            rows = cur.fetchall()

    if not rows:
        return {"patched": 0, "not_found": ids_to_patch}

    embedder = get_shared_embedder(EMBEDDING_MODEL)

    # Build new embed_texts
    texts = []
    found_ids = []
    for rid, description, required_args in rows:
        req_names = " ".join(
            a["name"] for a in (required_args or []) if isinstance(a, dict) and "name" in a
        )
        base = (
            f"{rid} {rid} terraform resource "
            f"{description or ''} "
            f"required arguments: {req_names}"
        ).strip()
        extra = semantic_tags.get(rid, "")
        embed_text = f"{base} {extra}".strip() if extra else base
        texts.append(embed_text)
        found_ids.append((rid, embed_text))

    # Encode in batch
    embeddings = embedder.encode(
        [t for t in texts],
        batch_size=32,
        show_progress_bar=False,
        convert_to_numpy=True,
    ).tolist()

    # Update DB
    patched = 0
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            for (rid, embed_text), emb in zip(found_ids, embeddings):
                cur.execute(
                    """
                    UPDATE terraform_resources
                       SET embed_text = %s, embedding = %s::vector, updated_at = NOW()
                     WHERE id = %s
                    """,
                    (embed_text, emb, rid),
                )
                patched += cur.rowcount

    not_found = [rid for rid in ids_to_patch if rid not in {r[0] for r in rows}]
    logger.info("re-embed: patched %d resources, %d not in DB", patched, len(not_found))
    return {"patched": patched, "not_found": not_found}


@router.post("/seed", dependencies=[Depends(require_api_key)])
async def seed_rag_corpus(
    background_tasks: BackgroundTasks,
    force: bool = False,
):
    """
    Seed the terraform_resources pgvector table.

    - force=false (default): skips providers whose data is already current
    - force=true: re-indexes everything from scratch
    """
    counts = _count_resources()
    total_existing = sum(counts.values())
    background_tasks.add_task(_run_seed, force)
    return JSONResponse(
        status_code=202,
        content={
            "message": "RAG seeding started in background",
            "force": force,
            "existing_resources": total_existing,
            "tip": "Poll GET /rag/status to track progress",
        },
    )


@router.post("/re-embed", dependencies=[Depends(require_api_key)])
async def re_embed_resources(
    background_tasks: BackgroundTasks,
    resource_ids: list[str] | None = None,
):
    """
    Patch embed_text with cross-cloud semantic tags and recompute embeddings.

    - resource_ids=null (default): patches all resources listed in _SEMANTIC_TAGS (~60 entries)
    - resource_ids=[...]: patches only the specified IDs

    Does NOT re-fetch docs from GitHub. Runs in background.
    """
    target = resource_ids or list(
        __import__("rag.corpus_builder", fromlist=["RAGBuilder"]).RAGBuilder._SEMANTIC_TAGS.keys()
    )
    background_tasks.add_task(_run_re_embed, resource_ids)
    return JSONResponse(
        status_code=202,
        content={
            "message": "Re-embedding started in background",
            "target_count": len(target),
            "tip": "Poll GET /rag/status or check logs for completion",
        },
    )


@router.post("/seed-patterns", dependencies=[Depends(require_api_key)])
async def seed_canonical_patterns(
    background_tasks: BackgroundTasks,
    force: bool = False,
):
    """
    Seed tf_canonical_patterns with curated HCL architecture templates.

    - force=false (default): upsert only — keeps existing patterns unchanged
    - force=true: deletes all existing patterns before inserting

    Runs in background. ~14 patterns, takes <5s.
    """
    def _run() -> None:
        from rag.seed_canonical_patterns import seed_patterns
        n = seed_patterns(force=force)
        logger.info("seed-patterns: %d upserted", n)

    background_tasks.add_task(_run)
    return JSONResponse(
        status_code=202,
        content={
            "message": "Canonical pattern seeding started",
            "force": force,
            "tip": "Poll GET /rag/status to see pattern count",
        },
    )


@router.post("/update", dependencies=[Depends(require_api_key)])
async def update_rag_corpus(
    background_tasks: BackgroundTasks,
    force: bool = False,
):
    """
    Check for new Terraform provider doc commits on GitHub and re-index only
    the providers that changed (incremental update via rag_commits SHA tracking).

    - force=false (default): skips providers already up to date
    - force=true: equivalent to /seed?force=true (full rebuild)

    Uses RAGUpdater.check_and_update() — the component that was implemented
    but never exposed via API.
    """
    def _run_update(force_rebuild: bool) -> None:
        try:
            from rag.updater import RAGUpdater
            updater = RAGUpdater(github_token=os.environ.get("GITHUB_TOKEN", ""))
            if force_rebuild:
                result = updater.force_rebuild()
            else:
                result = updater.check_and_update()
            logger.info("RAG update completed: %s", result)
        except Exception as exc:
            logger.error("RAG update failed: %s", exc, exc_info=True)

    background_tasks.add_task(_run_update, force)
    return JSONResponse(
        status_code=202,
        content={
            "message": "RAG incremental update started in background",
            "force": force,
            "tip": "Poll GET /rag/status to track progress. Only changed providers are re-indexed.",
        },
    )


@router.get("/status")
async def rag_status():
    """Return the number of indexed Terraform resources per provider."""
    counts = _count_resources()
    total = sum(counts.values())
    pattern_count = 0
    try:
        from rag.rag_config import get_pg_connection
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tf_canonical_patterns")
                pattern_count = cur.fetchone()[0]
    except Exception:
        pass
    return {
        "total": total,
        "by_provider": counts,
        "ready": total > 0,
        "canonical_patterns": pattern_count,
    }
