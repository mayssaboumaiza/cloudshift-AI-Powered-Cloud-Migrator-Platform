"""
dynamic_tag_enricher.py — LLM-powered semantic tag generation for unknown Terraform resources.

When lookup_terraform_mapping finds a resource NOT in the static _SEMANTIC_TAGS dict,
this module calls GPT-4o to generate a cross-cloud equivalence description, stores it
in the dynamic_semantic_tags table, and re-embeds the resource in terraform_resources.

Public API:
    enrich_if_missing(resource_id, provider, description, target_cloud) -> str | None
        Returns the generated tag string, or None if already enriched / generation failed.

    get_dynamic_tag(resource_id) -> str | None
        Returns the stored dynamic tag for a resource, or None.

    enrich_all_missing(source_cloud, target_cloud, limit) -> int
        Batch-enrich all resources in terraform_resources that lack a tag. Returns count.
"""

from __future__ import annotations

import json
import logging
import os
import threading

logger = logging.getLogger("DynamicTagEnricher")

_lock = threading.Lock()

# ── LLM prompt ────────────────────────────────────────────────────────────────
_TAG_SYSTEM = (
    "You are a cloud architecture expert. Given a Terraform resource type and its description, "
    "generate a short space-separated list of keywords (max 25 words) that describe its "
    "functional equivalence to the target cloud provider. "
    "Format: '<resource_id> equivalent <target_cloud> <keywords>'. "
    "Be specific about the target service name. No explanations, just keywords."
)

_TAG_USER_TMPL = (
    "Terraform resource: {resource_id}\n"
    "Provider: {source_cloud}\n"
    "Description: {description}\n"
    "Target cloud: {target_cloud}\n\n"
    "Generate cross-cloud equivalence keywords for this resource targeting {target_cloud}. "
    "Example format: 'aws_sqs_queue equivalent azure service bus queue async message decoupled azurerm_servicebus_queue'"
)


def _get_llm():
    """Lazy-init Azure OpenAI client."""
    try:
        from langchain_openai import AzureChatOpenAI
        import httpx
        # This module uses the RAG deployment (gpt-4o), which lives on a
        # SEPARATE Azure endpoint than the agents. Read the _RAG vars first,
        # fall back to the global ones — same logic as llm_config._get_llm_rag.
        endpoint = (
            os.getenv("AZURE_AI_ENDPOINT_RAG")
            or os.getenv("AZURE_AI_ENDPOINT", "")
        ).strip()
        api_key = (
            os.getenv("AZURE_AI_API_KEY_RAG")
            or os.getenv("AZURE_AI_API_KEY", "")
        ).strip()
        if not endpoint or not api_key:
            return None
        from configuration.settings import settings
        return AzureChatOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            azure_deployment=(
                os.getenv("AZURE_MODEL_RAG")
                or os.getenv("AZURE_MODEL")
                or settings.AZURE_MODEL_RAG
            ),
            api_version=(
                os.getenv("AZURE_OPENAI_API_VERSION_RAG")
                or os.getenv("AZURE_OPENAI_API_VERSION")
                or settings.AZURE_OPENAI_API_VERSION
            ),
            temperature=0,
            max_tokens=80,
            timeout=httpx.Timeout(10.0),
            max_retries=0,
        )
    except Exception as exc:
        logger.warning("DynamicTagEnricher: LLM init failed — %s", exc)
        return None


def _ensure_table() -> None:
    """Create dynamic_semantic_tags table if it doesn't exist."""
    try:
        from rag.rag_config import get_pg_connection
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS dynamic_semantic_tags (
                        resource_id   TEXT PRIMARY KEY,
                        source_cloud  TEXT NOT NULL,
                        target_cloud  TEXT NOT NULL,
                        tag_text      TEXT NOT NULL,
                        created_at    TIMESTAMPTZ DEFAULT NOW(),
                        model_used    TEXT
                    )
                """)
                conn.commit()
    except Exception as exc:
        logger.warning("DynamicTagEnricher: table init failed — %s", exc)


_table_initialized = False


def _init_table_once() -> None:
    global _table_initialized
    if not _table_initialized:
        _ensure_table()
        _table_initialized = True


def get_dynamic_tag(resource_id: str) -> str | None:
    """Return the stored dynamic tag for a resource, or None if not found."""
    _init_table_once()
    try:
        from rag.rag_config import get_pg_connection
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tag_text FROM dynamic_semantic_tags WHERE resource_id = %s",
                    (resource_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as exc:
        logger.debug("get_dynamic_tag(%s): %s", resource_id, exc)
        return None


def _store_tag(resource_id: str, source_cloud: str, target_cloud: str,
               tag_text: str, model: str) -> None:
    try:
        from rag.rag_config import get_pg_connection
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO dynamic_semantic_tags
                        (resource_id, source_cloud, target_cloud, tag_text, model_used)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (resource_id) DO UPDATE SET
                        tag_text   = EXCLUDED.tag_text,
                        model_used = EXCLUDED.model_used,
                        created_at = NOW()
                """, (resource_id, source_cloud, target_cloud, tag_text, model))
                conn.commit()
    except Exception as exc:
        logger.warning("_store_tag(%s): %s", resource_id, exc)


def _re_embed_resource(resource_id: str, extra_tag: str) -> bool:
    """Re-compute and store the embedding for resource_id with the new tag appended."""
    try:
        from rag.rag_config import get_pg_connection, EMBEDDING_MODEL
        from core.embedding_model_loader import get_shared_embedder
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT embed_text FROM terraform_resources WHERE id = %s",
                    (resource_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                base_text = row[0] or ""
                # Avoid duplicating the tag if already present
                if extra_tag.lower() in base_text.lower():
                    return True
                new_text = f"{base_text} {extra_tag}".strip()
                embedder = get_shared_embedder(EMBEDDING_MODEL)
                new_emb = embedder.encode([new_text]).tolist()[0]
                cur.execute(
                    "UPDATE terraform_resources SET embed_text = %s, embedding = %s::vector WHERE id = %s",
                    (new_text, new_emb, resource_id),
                )
                conn.commit()
                logger.info("DynamicTagEnricher: re-embedded '%s' with dynamic tag", resource_id)
                return True
    except Exception as exc:
        logger.warning("_re_embed_resource(%s): %s", resource_id, exc)
        return False


def enrich_if_missing(
    resource_id: str,
    source_cloud: str,
    description: str,
    target_cloud: str = "azure",
) -> str | None:
    """
    Generate and store a cross-cloud semantic tag for resource_id if not already present.

    Returns the tag string if newly generated, None if already exists or generation failed.
    Thread-safe: uses a lock to prevent duplicate LLM calls for the same resource.
    """
    _init_table_once()

    # Check static tags first — no LLM call needed
    try:
        from rag.corpus_builder import RAGBuilder
        if resource_id in RAGBuilder._SEMANTIC_TAGS:
            return None  # Already covered by static tags
    except Exception:
        pass

    # Check DB cache
    existing = get_dynamic_tag(resource_id)
    if existing:
        return None  # Already enriched

    with _lock:
        # Double-check inside lock
        existing = get_dynamic_tag(resource_id)
        if existing:
            return None

        llm = _get_llm()
        if not llm:
            return None

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            prompt = _TAG_USER_TMPL.format(
                resource_id=resource_id,
                source_cloud=source_cloud,
                description=description[:300],
                target_cloud=target_cloud,
            )
            resp = llm.invoke([
                SystemMessage(content=_TAG_SYSTEM),
                HumanMessage(content=prompt),
            ])
            tag_text = (resp.content or "").strip()

            if not tag_text or len(tag_text) < 10:
                logger.warning("DynamicTagEnricher: empty/short tag for '%s'", resource_id)
                return None

            # Truncate to max 200 chars
            tag_text = tag_text[:200]
            from configuration.settings import settings
            model_name = (
                os.getenv("AZURE_MODEL_RAG")
                or os.getenv("AZURE_MODEL")
                or settings.AZURE_MODEL_RAG
            )
            _store_tag(resource_id, source_cloud, target_cloud, tag_text, model_name)
            _re_embed_resource(resource_id, tag_text)
            # Feedback loop: absorb into cloud_service_ontology for future migrations
            try:
                from rag.cloud_ontology import absorb_dynamic_tag
                absorb_dynamic_tag(source_cloud, resource_id, target_cloud, tag_text)
            except Exception as _ont_err:
                logger.debug("DynamicTagEnricher: ontology absorption failed — %s", _ont_err)
            logger.info(
                "DynamicTagEnricher: enriched '%s' → '%s'", resource_id, tag_text[:80]
            )
            return tag_text

        except Exception as exc:
            logger.warning("DynamicTagEnricher: LLM call failed for '%s' — %s", resource_id, exc)
            return None


def enrich_all_missing(
    source_cloud: str = "aws",
    target_cloud: str = "azure",
    limit: int = 50,
    resource_ids: list[str] | None = None,
) -> int:
    """
    Enrich resources that lack a static or dynamic tag via LLM.

    resource_ids: if provided, only enrich these specific IDs (migration-scoped).
                  If None, falls back to all untagged resources up to limit.
    Returns the number of resources enriched.
    """
    _init_table_once()

    try:
        from rag.rag_config import get_pg_connection
        from rag.corpus_builder import RAGBuilder
        static_ids = set(RAGBuilder._SEMANTIC_TAGS.keys())

        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                if resource_ids:
                    # Scoped: only the resources detected in this migration
                    cur.execute("""
                        SELECT r.id, r.description
                        FROM terraform_resources r
                        WHERE r.id = ANY(%s)
                          AND r.id NOT IN (
                              SELECT resource_id FROM dynamic_semantic_tags
                          )
                        ORDER BY r.id
                    """, (resource_ids,))
                else:
                    provider_key = source_cloud if source_cloud != "azure" else "azurerm"
                    cur.execute("""
                        SELECT r.id, r.description
                        FROM terraform_resources r
                        WHERE r.provider = %s
                          AND r.id NOT IN (
                              SELECT resource_id FROM dynamic_semantic_tags
                          )
                        ORDER BY r.id
                        LIMIT %s
                    """, (provider_key, limit))
                rows = cur.fetchall()
    except Exception as exc:
        logger.error("enrich_all_missing: DB query failed — %s", exc)
        return 0

    enriched = 0
    for resource_id, description in rows:
        if resource_id in static_ids:
            continue
        tag = enrich_if_missing(resource_id, source_cloud, description or "", target_cloud)
        if tag:
            enriched += 1

    scope = f"{len(resource_ids)} scoped IDs" if resource_ids else "full scan"
    logger.info("DynamicTagEnricher: batch complete — %d/%d enriched (%s)", enriched, len(rows), scope)
    return enriched
