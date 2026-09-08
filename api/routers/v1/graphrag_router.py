"""
graphrag_router.py — FastAPI endpoints for GraphRAG knowledge graph visualization.

Endpoints:
  GET  /api/graph-rag/visualize                    → nodes + edges for force-directed graph
  GET  /api/graph-rag/stats                        → knowledge graph statistics
  GET  /api/graph-rag/context/{resource}           → doc chunks + companions for one resource
  GET  /api/graph-rag/chat-history/{migration_id}  → load persistent chat history
  POST /api/graph-rag/chat-history/{migration_id}  → save one message (user or assistant)
"""
import os
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/graph-rag", tags=["GRAPH_RAG"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_db_conn():
    import psycopg2, psycopg2.extras
    return psycopg2.connect(
        f"host={os.getenv('DB_HOST','localhost')} port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
        f"user={os.getenv('DB_USER','postgres')} password={os.getenv('DB_PASSWORD','postgres')}",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


# ── persistent chat history ───────────────────────────────────────────────────

class SaveMessageRequest(BaseModel):
    role: str           # "user" | "assistant"
    content: str
    sources_count: int = 0


@router.get("/chat-history/{migration_id}")
def get_chat_history(migration_id: str):
    """Return all saved RAG messages for a migration, oldest first."""
    try:
        conn = _get_db_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, role, content, sources_count, created_at "
                "FROM rag_messages WHERE migration_id = %s ORDER BY id ASC",
                (migration_id,),
            )
            rows = cur.fetchall()
        conn.close()
        return {
            "migration_id": migration_id,
            "messages": [
                {
                    "id": r["id"],
                    "role": r["role"],
                    "content": r["content"],
                    "sources": r["sources_count"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in rows
            ],
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/chat-history/{migration_id}", status_code=201)
def save_message(migration_id: str, body: SaveMessageRequest):
    """Persist one RAG message (user or assistant) for a migration."""
    if body.role not in ("user", "assistant"):
        return JSONResponse(status_code=422, content={"error": "role must be 'user' or 'assistant'"})
    try:
        conn = _get_db_conn()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rag_messages (migration_id, role, content, sources_count) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (migration_id, body.role, body.content, body.sources_count),
            )
            row = cur.fetchone()
        conn.close()
        return {"id": row["id"], "migration_id": migration_id, "role": body.role}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


class RagChatMessage(BaseModel):
    role: str   # "user" or "assistant"
    content: str


class RagChatRequest(BaseModel):
    query: str
    migration_id: str | None = None          # personalise context to this migration
    resources: list[str] | None = None       # explicit resource list for context
    history: list[RagChatMessage] | None = None   # prior turns


def _get_rag():
    try:
        from rag.graph_rag import TerraformGraphRAG
        return TerraformGraphRAG.get_instance()
    except Exception:
        return None


@router.get("/visualize")
def get_graph_visualization(
    provider: str = Query(default="", description="Filter by provider: aws | google | azurerm"),
    limit: int = Query(default=120, ge=10, le=500, description="Max nodes to return"),
):
    """Return graph nodes + edges in Cytoscape/D3-compatible format."""
    rag = _get_rag()
    if rag is None:
        return JSONResponse(status_code=503, content={"nodes": [], "edges": [], "stats": {"available": False}})
    try:
        return rag.graph_to_vis(provider=provider or None, limit=limit)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"nodes": [], "edges": [], "stats": {"reason": str(exc)}})


@router.get("/provider-graph")
def get_provider_graph(
    provider: str = Query(..., description="aws | azurerm | google"),
    top_n: int = Query(default=60, ge=10, le=200, description="Top N most-connected resources"),
):
    """Return the top N most-connected resources for a cloud provider + their relationships.

    Nodes are ranked by degree (number of RELATED_TO + REFERENCES edges).
    Each node includes: name, description, required_args (first 5), service category.
    Each edge includes: source, target, relation type.
    """
    import os, psycopg2, psycopg2.extras, json as _json
    dsn = (
        f"host={os.getenv('DB_HOST','localhost')} port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
        f"user={os.getenv('DB_USER','postgres')} password={os.getenv('DB_PASSWORD','postgres')}"
    )
    try:
        conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        with conn.cursor() as cur:
            # Top N most connected resources for this provider
            cur.execute("""
                SELECT r.id, r.provider, r.description,
                       r.required_args, r.optional_args,
                       count(rel.source) as degree
                FROM terraform_resources r
                LEFT JOIN resource_relations rel
                  ON rel.source = r.id OR rel.target = r.id
                WHERE r.provider = %s
                GROUP BY r.id, r.provider, r.description, r.required_args, r.optional_args
                ORDER BY degree DESC
                LIMIT %s
            """, (provider, top_n))
            rows = cur.fetchall()

            nodes = []
            node_ids = set()
            for row in rows:
                rid = row["id"]
                node_ids.add(rid)
                # Extract service category from resource name: aws_IAM_role → iam
                parts = rid.split("_")
                service = parts[1] if len(parts) > 2 else parts[-1]
                # Parse required_args
                raw_args = row["required_args"] or []
                if isinstance(raw_args, str):
                    try: raw_args = _json.loads(raw_args)
                    except Exception: raw_args = []
                arg_names = [a["name"] if isinstance(a, dict) else str(a) for a in raw_args[:5]]
                nodes.append({
                    "id": rid,
                    "label": rid,
                    "provider": row["provider"],
                    "service": service,
                    "description": (row["description"] or "")[:150],
                    "required_args": arg_names,
                    "degree": row["degree"],
                })

            # Relationships between these top N nodes only
            cur.execute("""
                SELECT source, target, relation
                FROM resource_relations
                WHERE source = ANY(%s) AND target = ANY(%s)
                ORDER BY relation, source
            """, (list(node_ids), list(node_ids)))
            edges = [{"source": r["source"], "target": r["target"], "type": r["relation"]} for r in cur.fetchall()]

        conn.close()
        return {"nodes": nodes, "edges": edges, "provider": provider, "stats": {"node_count": len(nodes), "edge_count": len(edges)}}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc), "nodes": [], "edges": []})


@router.get("/migration-graph/{migration_id}")
def get_migration_graph(migration_id: str):
    """Return the migration-specific knowledge subgraph.

    Returns:
    - source_nodes: AWS/GCP resources from the migration plan (with their KB neighbors)
    - target_nodes: Azure resources from the plan
    - migration_edges: source → target mappings with strategy and score
    - internal_edges: RELATED_TO/REFERENCES between source resources
    - target_edges: relationships between target resources
    """
    import os, psycopg2, psycopg2.extras, json as _json
    dsn = (
        f"host={os.getenv('DB_HOST','localhost')} port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
        f"user={os.getenv('DB_USER','postgres')} password={os.getenv('DB_PASSWORD','postgres')}"
    )
    try:
        conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = True
        with conn.cursor() as cur:
            # Fetch migration plan
            cur.execute("SELECT migration_plan, source_cloud, target_cloud FROM migrations WHERE id = %s", (migration_id,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return JSONResponse(status_code=404, content={"error": "Migration not found"})

            raw_plan = row["migration_plan"] or {}
            source_cloud = (row["source_cloud"] or "aws").lower()
            target_cloud = (row["target_cloud"] or "azure").lower()

            if isinstance(raw_plan, str):
                try: raw_plan = _json.loads(raw_plan)
                except Exception: raw_plan = {}

            # migration_plan may be a list OR a dict with a 'resources' key
            if isinstance(raw_plan, dict):
                plan_items = raw_plan.get("resources", [])
            else:
                plan_items = raw_plan if isinstance(raw_plan, list) else []

            _PREFIXES = ("aws_", "azurerm_", "google_", "kubernetes_", "helm_")

            def _is_tf(name):
                return any(name.startswith(p) for p in _PREFIXES)

            # Extract source → target mappings; resolve logical names to Terraform IDs
            mappings = []
            for item in plan_items:
                raw_src = item.get("source_service") or item.get("source_type") or item.get("service_name", "")
                tgt = item.get("terraform_resource") or item.get("target_equivalent") or item.get("target_service", "")
                if not raw_src or not tgt:
                    continue
                # Resolve source to a Terraform resource ID if possible
                if _is_tf(raw_src):
                    src_tf = raw_src
                else:
                    provider_key = source_cloud if source_cloud != "azure" else "azurerm"
                    cur.execute(
                        "SELECT id FROM terraform_resources WHERE provider = %s AND id LIKE %s ORDER BY id LIMIT 1",
                        (provider_key, f"{provider_key}_{raw_src}%"),
                    )
                    hit = cur.fetchone()
                    src_tf = hit["id"] if hit else raw_src
                mappings.append({
                    "source": src_tf,
                    "source_label": raw_src,
                    "target": tgt,
                    "strategy": item.get("strategy_7r") or item.get("strategy", "REHOST"),
                    "score": item.get("equivalence_score", 0),
                    "category": item.get("category", ""),
                    "effort_days": item.get("effort_days", 0),
                    "monthly_cost_eur": item.get("monthly_cost_eur", 0),
                })

            source_ids = list({m["source"] for m in mappings})
            target_ids = list({m["target"] for m in mappings})
            all_ids = list(set(source_ids + target_ids))

            # Fetch node metadata from KB
            nodes_map = {}
            if all_ids:
                cur.execute("""
                    SELECT id, provider, description, required_args
                    FROM terraform_resources WHERE id = ANY(%s)
                """, (all_ids,))
                for n in cur.fetchall():
                    raw_args = n["required_args"] or []
                    if isinstance(raw_args, str):
                        try: raw_args = _json.loads(raw_args)
                        except Exception: raw_args = []
                    arg_names = [a["name"] if isinstance(a, dict) else str(a) for a in raw_args[:5]]
                    nodes_map[n["id"]] = {
                        "id": n["id"], "label": n["id"],
                        "provider": n["provider"],
                        "description": (n["description"] or "")[:150],
                        "required_args": arg_names,
                        "in_kb": True,
                    }

            # Build label_map: tf_id → original logical label from plan
            label_map = {m["source"]: m.get("source_label", m["source"]) for m in mappings}
            # Build strategy_map: source_tf → strategy info
            strategy_map = {m["source"]: m for m in mappings}

            def make_node(rid, role, cloud):
                if rid in nodes_map:
                    n = dict(nodes_map[rid])
                else:
                    n = {"id": rid, "label": rid, "provider": cloud, "description": "", "required_args": [], "in_kb": False}
                n["role"] = role
                # Add display label (logical name when different from Terraform ID)
                logical = label_map.get(rid, rid)
                n["display_label"] = logical if logical != rid else rid
                # Add strategy/score for source nodes
                if role == "source" and rid in strategy_map:
                    m = strategy_map[rid]
                    n["strategy"]         = m["strategy"]
                    n["score"]            = m["score"]
                    n["effort_days"]      = m["effort_days"]
                    n["monthly_cost_eur"] = m["monthly_cost_eur"]
                    n["category"]         = m["category"]
                return n

            source_nodes = [make_node(s, "source", source_cloud) for s in source_ids]
            target_nodes = [make_node(t, "target", target_cloud) for t in target_ids]

            # Internal KB relationships between source resources
            internal_edges = []
            if len(source_ids) > 1:
                cur.execute("""
                    SELECT source, target, relation FROM resource_relations
                    WHERE source = ANY(%s) AND target = ANY(%s)
                """, (source_ids, source_ids))
                internal_edges = [{"source": r["source"], "target": r["target"], "type": r["relation"]} for r in cur.fetchall()]

            # Relationships between target resources
            target_edges = []
            if len(target_ids) > 1:
                cur.execute("""
                    SELECT source, target, relation FROM resource_relations
                    WHERE source = ANY(%s) AND target = ANY(%s)
                """, (target_ids, target_ids))
                target_edges = [{"source": r["source"], "target": r["target"], "type": r["relation"]} for r in cur.fetchall()]

        conn.close()
        return {
            "migration_id": migration_id,
            "source_cloud": source_cloud,
            "target_cloud": target_cloud,
            "source_nodes": source_nodes,
            "target_nodes": target_nodes,
            "migration_edges": mappings,
            "internal_edges": internal_edges,
            "target_edges": target_edges,
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/stats")
def get_graph_stats():
    """Return knowledge graph statistics (total nodes, edges, providers)."""
    rag = _get_rag()
    if rag is None:
        return {"available": False, "reason": "RAG unavailable"}
    return rag.get_graph_stats()


@router.get("/context/{resource_name}")
def get_resource_context(
    resource_name: str,
    provider: str = Query(default=""),
    hops: int = Query(default=2, ge=1, le=4, description="Multi-hop depth (1=direct, 2=2-hop, ...)"),
):
    """Return enriched doc context + multi-hop traversal for a Terraform resource."""
    rag = _get_rag()
    if rag is None:
        return JSONResponse(status_code=503, content={"error": "RAG unavailable"})
    try:
        context = rag.get_context(resource_name, provider or resource_name.split("_")[0])
        traversal = rag.get_traversal_for_resource(resource_name, max_hops=hops)
        return {"resource": resource_name, "context": context, "traversal": traversal}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/communities")
def get_communities(
    provider: str = Query(default="", description="Filter by provider: aws | google | azurerm"),
):
    """Return community summaries detected via Louvain algorithm.

    Used by the React ForceGraph to color nodes by community.
    """
    rag = _get_rag()
    if rag is None:
        return {"communities": [], "total_communities": 0, "available": False, "reason": "RAG unavailable"}
    return rag.get_communities(provider or None)


_GRAPH_KEYWORDS = (
    "graphe", "graph", "dépendance", "dependance", "dependency", "dependencies",
    "relation", "lien", "connexion", "montre", "affiche", "visualise", "visualize",
    "show", "display", "network", "topologie", "topology", "subgraph",
)

def _is_graph_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _GRAPH_KEYWORDS)


def _build_graph_data(rag, migration_id: str | None, resources: list[str] | None) -> dict | None:
    """Return graph_data {nodes, edges} for the chat response when query is graph-related."""
    try:
        import psycopg2, psycopg2.extras, json as _json

        dsn = (
            f"host={os.getenv('DB_HOST','localhost')} port={os.getenv('DB_PORT','5432')} "
            f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
            f"user={os.getenv('DB_USER','postgres')} password={os.getenv('DB_PASSWORD','postgres')}"
        )

        # Migration graph (source → target) when migration_id provided
        if migration_id:
            conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT migration_plan, source_cloud, target_cloud FROM migrations WHERE id = %s",
                        (migration_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    raw = row["migration_plan"] or {}
                    if isinstance(raw, str):
                        raw = _json.loads(raw)
                    if isinstance(raw, dict):
                        plan_items = raw.get("resources") or raw.get("services") or []
                    else:
                        plan_items = raw if isinstance(raw, list) else []
                    source_cloud = (row["source_cloud"] or "aws").lower()
                    target_cloud = (row["target_cloud"] or "azure").lower()
            finally:
                conn.close()

            nodes, edges = [], []
            seen = set()
            for item in plan_items[:20]:
                src = item.get("source_service") or item.get("service_name", "")
                tgt = item.get("terraform_resource") or item.get("target_equivalent") or item.get("target_service", "")
                strategy = item.get("strategy_7r") or item.get("strategy", "REHOST")
                if not src or not tgt:
                    continue
                if src not in seen:
                    nodes.append({"id": src, "label": src, "provider": source_cloud, "role": "source", "strategy": strategy})
                    seen.add(src)
                if tgt not in seen:
                    nodes.append({"id": tgt, "label": tgt, "provider": target_cloud, "role": "target"})
                    seen.add(tgt)
                edges.append({"source": src, "target": tgt, "type": "MIGRATES_TO", "strategy": strategy})

            return {"nodes": nodes, "edges": edges, "type": "migration"} if nodes else None

        # Resource dependency graph when explicit resources provided
        if resources:
            conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, provider, description FROM terraform_resources WHERE id = ANY(%s)",
                        (resources[:12],),
                    )
                    res_rows = cur.fetchall()
                    cur.execute(
                        "SELECT source, target, relation FROM resource_relations "
                        "WHERE source = ANY(%s) OR target = ANY(%s)",
                        (resources[:12], resources[:12]),
                    )
                    rel_rows = cur.fetchall()
            finally:
                conn.close()

            nodes = [{"id": r["id"], "label": r["id"], "provider": r["provider"] or "", "role": "resource"} for r in res_rows]
            edges = [{"source": r["source"], "target": r["target"], "type": r["relation"]} for r in rel_rows]
            return {"nodes": nodes, "edges": edges, "type": "dependency"} if nodes else None

    except Exception:
        pass
    return None


@router.post("/chat")
async def rag_chat(body: RagChatRequest):
    """Conversational GraphRAG assistant.

    Personalises the RAG context to the current migration when migration_id is
    provided — otherwise falls back to the top matching knowledge-base chunks.
    Returns graph_data (nodes + edges) when the query is graph/dependency-related.
    Uses Azure OpenAI (same config as Agent 02) so no extra credentials needed.
    """
    rag = _get_rag()
    if rag is None:
        return JSONResponse(status_code=503, content={"error": "RAG unavailable"})

    # ── Build context chunks ──────────────────────────────────────────────────
    contexts: list[str] = []

    if body.migration_id:
        try:
            import psycopg2, psycopg2.extras, json as _json

            def _get_plan(mid: str):
                conn = psycopg2.connect(
                    f"host={os.getenv('DB_HOST','localhost')} "
                    f"port={os.getenv('DB_PORT','5432')} "
                    f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
                    f"user={os.getenv('DB_USER','postgres')} "
                    f"password={os.getenv('DB_PASSWORD','postgres')}",
                    cursor_factory=psycopg2.extras.RealDictCursor,
                )
                conn.autocommit = True
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT migration_plan FROM migrations WHERE id = %s", (mid,))
                        row = cur.fetchone()
                        if row and row["migration_plan"]:
                            raw = row["migration_plan"]
                            return raw if isinstance(raw, dict) else _json.loads(raw)
                finally:
                    conn.close()

            import asyncio
            plan = await asyncio.to_thread(_get_plan, body.migration_id)
            if plan:
                plan_contexts = rag.get_contexts_for_plan(plan)
                contexts = list(plan_contexts.values())[:6]
        except Exception:
            pass

    if not contexts and body.resources:
        for r in body.resources[:6]:
            try:
                ctx = rag.get_context(r, r.split("_")[0])
                if ctx:
                    contexts.append(ctx)
            except Exception:
                pass

    context_text = "\n\n---\n\n".join(contexts) if contexts else ""

    # ── Detect graph query → fetch graph_data in parallel ────────────────────
    wants_graph = _is_graph_query(body.query)
    graph_data = None
    if wants_graph:
        import asyncio
        graph_data = await asyncio.to_thread(
            _build_graph_data, rag, body.migration_id, body.resources
        )

    # ── Call Azure OpenAI ─────────────────────────────────────────────────────
    try:
        from agents.iac_generator.llm_config import _get_llm_rag as get_llm
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

        system_content = (
            "You are an expert cloud migration assistant with access to a Terraform knowledge graph. "
            "Answer questions about cloud migration strategies, Terraform resources, service equivalences, "
            "dependencies, and best practices. Be concise and use **Markdown** formatting: "
            "use **bold**, bullet lists, code blocks, and tables where appropriate.\n\n"
            "When the user asks about dependencies or graphs, describe the relationships clearly "
            "— a visual graph will be shown automatically alongside your answer."
        )
        if context_text:
            system_content += f"\n\nKnowledge graph context for this migration:\n{context_text}"

        messages = [SystemMessage(content=system_content)]
        for msg in (body.history or []):
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            else:
                messages.append(AIMessage(content=msg.content))
        messages.append(HumanMessage(content=body.query))

        llm = get_llm()
        response = await llm.ainvoke(messages)

        result = {
            "answer": response.content,
            "sources_count": len(contexts),
            "migration_id": body.migration_id,
        }
        if graph_data:
            result["graph_data"] = graph_data
        return result

    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.get("/health")
def get_rag_health():
    """RAG infrastructure health check.

    Returns availability, node/edge count, and DB reachability.
    Used by the frontend badge and by CI smoke tests.
    """
    rag = _get_rag()
    if rag is None:
        return JSONResponse(
            status_code=503,
            content={"available": False, "reason": "GraphRAG could not be initialised — check pgvector and DB connection"},
        )
    try:
        stats = rag.get_graph_stats()
        node_count = stats.get("resource_count", 0) or stats.get("total_nodes", 0)
        return {
            "available": True,
            "node_count": node_count,
            "edge_count": stats.get("total_edges", 0),
            "providers": stats.get("providers", []),
            "populated": node_count > 0,
        }
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"available": False, "reason": str(exc)},
        )
