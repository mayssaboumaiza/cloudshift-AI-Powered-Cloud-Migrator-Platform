"""
graph_rag.py — GraphRAG Terraform on PostgreSQL + pgvector + Neo4j.

Public API:
    rag = TerraformGraphRAG.get_instance()
    rag.get_context(resource_name, provider)          → str
    rag.get_contexts_for_plan(migration_plan)         → dict[str, str]
    rag.get_graph_stats()                             → dict
    rag.get_traversal_for_resource(resource_name)     → dict
    rag.get_traversal_for_plan(migration_plan)        → dict
    rag.graph_rag_query(provider, target_resource, ...) → dict
    rag.rebuild()                                     → None

Storage:
    • terraform_resources     — doc chunks + embeddings (vector column) [PostgreSQL]
    • resource_relations      — directed resource graph [PostgreSQL + Neo4j]
    • tf_canonical_patterns   — pattern library (optional, migration 002) [PostgreSQL]
    • rag_commits             — last-indexed GitHub commit per provider [PostgreSQL]

Graph traversal:
    Neo4j (Cypher) is used when available — faster path queries and richer context.
    Falls back to PostgreSQL WITH RECURSIVE if Neo4j is unreachable.
"""

import json
import logging
from typing import Optional

from rag.rag_config import EMBEDDING_MODEL, get_pg_connection, neo4j_available, get_neo4j_driver
from rag.companions import COMPANIONS as _COMPANIONS
from core.embedding_model_loader import init_sentence_transformer

logger = logging.getLogger("GraphRAG")


class TerraformGraphRAG:
    """GraphRAG Terraform : recherche vectorielle pgvector + traversée SQL.

    Usage (singleton) :
        rag = TerraformGraphRAG.get_instance()
        context = rag.get_context("google_storage_bucket", "google")
        contexts = rag.get_contexts_for_plan(migration_plan)
        stats = rag.get_graph_stats()
        traversal = rag.get_traversal_for_resource("aws_s3_bucket")
    """

    _instance: Optional["TerraformGraphRAG"] = None

    @classmethod
    def get_instance(cls) -> "TerraformGraphRAG":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self.embedder = init_sentence_transformer(EMBEDDING_MODEL, logger=logger)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        p = (provider or "").strip().lower()
        return {"azure": "azurerm", "gcp": "google"}.get(p, p)

    def get_context(self, resource_name: str, provider: str) -> str:
        """Retourne le contexte enrichi : ressource principale + companions."""
        primary_meta = self._fetch_meta(resource_name, provider)
        if primary_meta is None:
            return self._fallback_context(resource_name)

        companions = self._get_companions(resource_name)
        companion_metas: list[tuple[str, dict]] = []
        for comp in companions:
            m = self._fetch_meta(comp, provider)
            if m:
                companion_metas.append((comp, m))

        return self._format_enriched(resource_name, primary_meta, companion_metas)

    def get_contexts_for_plan(self, migration_plan: dict) -> dict[str, str]:
        """Retourne {terraform_resource: context} pour toutes les ressources non-RETIRE/RETAIN."""
        contexts: dict[str, str] = {}
        for resource in migration_plan.get("resources", []):
            if resource.get("strategy") in ("RETIRE", "RETAIN"):
                continue
            target = resource.get("terraform_resource") or resource.get("target_service", "")
            provider = self._normalize_provider(resource.get("target_cloud", ""))
            if target and provider:
                contexts[target] = self.get_context(target, provider)
        return contexts

    def get_graph_stats(self) -> dict:
        """Retourne les statistiques du graphe pour le frontend."""
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT provider, COUNT(*) FROM terraform_resources GROUP BY provider"
                    )
                    providers = {row[0]: row[1] for row in cur.fetchall()}

                    cur.execute("SELECT COUNT(*) FROM terraform_resources")
                    total_resources = cur.fetchone()[0]

                    cur.execute(
                        "SELECT COUNT(*) FROM resource_relations WHERE relation = 'COMPANION'"
                    )
                    companion_edges = cur.fetchone()[0]

                    cur.execute(
                        "SELECT COUNT(*) FROM resource_relations WHERE relation = 'RELATED_TO'"
                    )
                    related_edges = cur.fetchone()[0]

                    cur.execute("SELECT COUNT(*) FROM resource_relations")
                    total_edges = cur.fetchone()[0]

                    # Community count (table may not exist yet)
                    community_count = 0
                    try:
                        cur.execute(
                            "SELECT COUNT(DISTINCT community_id) FROM resource_communities"
                        )
                        community_count = cur.fetchone()[0]
                    except Exception:
                        pass

                    # Argument node count (Nekrasov — tf_arguments, migration 004)
                    argument_node_count = 0
                    try:
                        cur.execute("SELECT COUNT(*) FROM tf_arguments")
                        argument_node_count = cur.fetchone()[0]
                    except Exception:
                        pass

            # Neo4j stats (optional)
            neo4j_stats: dict = {"available": False}
            try:
                if neo4j_available():
                    from rag.neo4j_sync import Neo4jSync
                    sync = Neo4jSync()
                    neo4j_stats = {"available": True, **sync.stats()}
                    sync.close()
            except Exception:
                pass

            return {
                "available": True,
                "total_nodes": total_resources,
                "total_edges": total_edges,
                "resource_count": total_resources,
                "providers": providers,
                "companion_edges": companion_edges,
                "related_edges": related_edges,
                "community_count": community_count,
                "argument_node_count": argument_node_count,
                "neo4j": neo4j_stats,
            }
        except Exception as e:
            logger.error(f"GraphRAG.get_graph_stats failed: {e}")
            return {"available": False, "reason": str(e)}

    def get_traversal_for_resource(self, resource_name: str, max_hops: int = 2) -> dict:
        """Multi-hop graph traversal for a resource (true GraphRAG, up to max_hops)."""
        multi_hop = self._multi_hop_traversal(resource_name, max_hops=max_hops)

        companions = [n["resource"] for n in multi_hop if n["relation"] in ("COMPANION", "DEPENDS_ON") and n["depth"] == 1]
        hop2_nodes = [n for n in multi_hop if n["depth"] == 2]
        related = [n["resource"] for n in multi_hop if n["relation"] == "RELATED_TO" and n["depth"] == 1]

        # Fallback to static companions if traversal is empty (DB not yet built)
        if not companions:
            companions = self._get_companions(resource_name)

        return {
            "resource": resource_name,
            "companions_fetched": companions,
            "related_resources": related,
            "multi_hop_nodes": multi_hop,
            "hop2_count": len(hop2_nodes),
            "max_hops": max_hops,
        }

    def get_traversal_for_plan(self, migration_plan: dict) -> dict:
        """Retourne les données de traversée pour toutes les ressources d'un plan."""
        traversals: dict[str, dict] = {}
        for resource in migration_plan.get("resources", []):
            if resource.get("strategy") in ("RETIRE", "RETAIN"):
                continue
            target = resource.get("terraform_resource") or resource.get("target_service", "")
            if target:
                traversals[target] = self.get_traversal_for_resource(target)
        return {
            "graph_stats": self.get_graph_stats(),
            "resource_traversals": traversals,
        }

    def get_argument_context(
        self,
        resource_name: str,
        query_text: str,
        top_k: int = 8,
    ) -> list[dict]:
        """Retrieve argument-level nodes for a resource (Nekrasov Graph RAG).

        Returns the top-k most relevant argument nodes for the given query.
        Falls back to an empty list if tf_arguments is not yet populated.

        Each entry: {arg_name, description, arg_type, is_required, similarity}
        """
        try:
            query_embedding = self.embedder.encode([query_text]).tolist()[0]
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    # First try: restrict to this specific resource
                    cur.execute(
                        """
                        SELECT arg_name, description, arg_type, is_required,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM tf_arguments
                        WHERE resource_id = %s
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (query_embedding, resource_name, query_embedding, top_k),
                    )
                    rows = cur.fetchall()
                    return [
                        {
                            "arg_name": r[0],
                            "description": r[1] or "",
                            "arg_type": r[2] or "",
                            "is_required": r[3],
                            "similarity": round(float(r[4]), 4),
                        }
                        for r in rows
                    ]
        except Exception as e:
            logger.debug(f"get_argument_context({resource_name}) failed: {e}")
            return []

    def get_migration_complexity(self, resource_name: str) -> dict:
        """Compute migration complexity for a resource using Neo4j graph topology.

        Complexity is derived from three graph signals (Nekrasov et al. 2025):
          1. dependency_depth   — longest shortest path from the resource to any leaf
                                  (Neo4j: shortestPath over DEPENDS_ON/COMPANION)
          2. required_arg_count — number of required arguments (HAS_ARGUMENT edges)
          3. companion_count    — number of COMPANION resources that must be co-generated

        Returns:
            {
              "resource":          str,
              "dependency_depth":  int,   — 0 = leaf, 1 = one level of deps, etc.
              "required_arg_count": int,
              "companion_count":   int,
              "complexity_score":  float, — weighted composite [0.0 – 1.0]
              "complexity_label":  str,   — "LOW" | "MEDIUM" | "HIGH"
              "source":            str,   — "neo4j" | "sql" | "fallback"
            }
        """
        if neo4j_available():
            return self._complexity_cypher(resource_name)
        return self._complexity_sql(resource_name)

    def _complexity_cypher(self, resource_name: str) -> dict:
        """Neo4j Cypher implementation of migration complexity scoring."""
        try:
            drv = get_neo4j_driver()
            with drv.session() as session:
                # Ensure index exists so MATCH on name is a lookup, not a full scan.
                # IF NOT EXISTS is idempotent — safe to run every time.
                session.run(
                    "CREATE INDEX resource_name IF NOT EXISTS FOR (r:Resource) ON (r.name)"
                )

                # Signal 1: dependency depth.
                # Check relationship existence first to avoid variable-length path
                # expansion when no DEPENDS_ON/COMPANION edges exist yet (Agent01
                # runs before tfgraph_importer creates these edges in Agent02).
                # REFERENCES is included: an HCL reference (X -> Y.attr) is a real
                # dependency. Without it, depth is almost always 0/1 because
                # DEPENDS_ON is never generated and COMPANION is sparse.
                has_edges = session.run(
                    """
                    MATCH (start:Resource {name: $name})
                    RETURN EXISTS {
                        (start)-[:DEPENDS_ON|COMPANION|REFERENCES]->()
                    } AS has_edges
                    """,
                    name=resource_name,
                ).single()
                if has_edges and has_edges["has_edges"]:
                    depth_result = session.run(
                        """
                        MATCH (start:Resource {name: $name})
                        OPTIONAL MATCH path = (start)-[:DEPENDS_ON|COMPANION|REFERENCES*1..5]->(leaf:Resource)
                        WHERE NOT (leaf)-[:DEPENDS_ON|COMPANION|REFERENCES]->()
                        RETURN COALESCE(MAX(length(path)), 0) AS depth
                        """,
                        name=resource_name,
                    )
                    depth_row = depth_result.single()
                    dependency_depth = int(depth_row["depth"]) if depth_row else 0
                else:
                    dependency_depth = 0

                # Signal 2: required argument count
                args_result = session.run(
                    """
                    MATCH (r:Resource {name: $name})-[:HAS_ARGUMENT]->(a:Argument {is_required: true})
                    RETURN COUNT(a) AS req_count
                    """,
                    name=resource_name,
                )
                args_row = args_result.single()
                required_arg_count = int(args_row["req_count"]) if args_row else 0

                # Signal 3: companion count (resources that must be co-generated)
                comp_result = session.run(
                    """
                    MATCH (r:Resource {name: $name})-[:COMPANION]->(c:Resource)
                    RETURN COUNT(c) AS comp_count
                    """,
                    name=resource_name,
                )
                comp_row = comp_result.single()
                companion_count = int(comp_row["comp_count"]) if comp_row else 0

            drv.close()
            return self._build_complexity_result(
                resource_name, dependency_depth, required_arg_count, companion_count, source="neo4j"
            )
        except Exception as e:
            logger.warning("_complexity_cypher(%s) failed: %s — falling back to SQL", resource_name, e)
            return self._complexity_sql(resource_name)

    def _complexity_sql(self, resource_name: str) -> dict:
        """PostgreSQL fallback for migration complexity scoring."""
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    # Signal 1: dependency depth via WITH RECURSIVE
                    cur.execute(
                        """
                        WITH RECURSIVE dep_tree(node, depth) AS (
                            SELECT target, 1
                            FROM resource_relations
                            WHERE source = %s AND relation IN ('DEPENDS_ON', 'COMPANION', 'REFERENCES')
                            UNION ALL
                            SELECT rr.target, dt.depth + 1
                            FROM resource_relations rr
                            JOIN dep_tree dt ON rr.source = dt.node
                            WHERE dt.depth < 5
                              AND rr.relation IN ('DEPENDS_ON', 'COMPANION', 'REFERENCES')
                        )
                        SELECT COALESCE(MAX(depth), 0) FROM dep_tree
                        """,
                        (resource_name,),
                    )
                    dependency_depth = int((cur.fetchone() or [0])[0])

                    # Signal 2: required arg count
                    cur.execute(
                        "SELECT COUNT(*) FROM tf_arguments WHERE resource_id = %s AND is_required = true",
                        (resource_name,),
                    )
                    required_arg_count = int((cur.fetchone() or [0])[0])

                    # Signal 3: companion count
                    cur.execute(
                        "SELECT COUNT(*) FROM resource_relations WHERE source = %s AND relation = 'COMPANION'",
                        (resource_name,),
                    )
                    companion_count = int((cur.fetchone() or [0])[0])

            return self._build_complexity_result(
                resource_name, dependency_depth, required_arg_count, companion_count, source="sql"
            )
        except Exception as e:
            logger.warning("_complexity_sql(%s) failed: %s", resource_name, e)
            return self._build_complexity_result(resource_name, 0, 0, 0, source="fallback")

    @staticmethod
    def _build_complexity_result(
        resource_name: str,
        dependency_depth: int,
        required_arg_count: int,
        companion_count: int,
        source: str,
    ) -> dict:
        """Compute weighted complexity score and label from the three graph signals."""
        # Normalise each signal to [0, 1] with empirical caps.
        # Depth cap is 8 (not 4): since REFERENCES edges are now traversed,
        # dependency chains routinely reach 4-5 hops, so a low cap saturated
        # the signal and pushed almost everything to HIGH. 8 restores spread.
        depth_norm   = min(dependency_depth / 8.0, 1.0)   # cap at 8 hops
        args_norm    = min(required_arg_count / 12.0, 1.0) # cap at 12 required args
        comp_norm    = min(companion_count / 4.0, 1.0)     # cap at 4 companions

        # Weighted composite: dependency depth matters most
        score = round(0.45 * depth_norm + 0.35 * args_norm + 0.20 * comp_norm, 3)

        # Thresholds raised (HIGH 0.65->0.55, MEDIUM 0.30->0.25 after the
        # depth-cap change) to keep a balanced LOW/MEDIUM/HIGH spread.
        if score >= 0.55:
            label = "HIGH"
        elif score >= 0.25:
            label = "MEDIUM"
        else:
            label = "LOW"

        return {
            "resource":           resource_name,
            "dependency_depth":   dependency_depth,
            "required_arg_count": required_arg_count,
            "companion_count":    companion_count,
            "complexity_score":   score,
            "complexity_label":   label,
            "source":             source,
        }

    def get_complexity_for_plan(self, migration_plan: dict) -> dict[str, dict]:
        """Return complexity scores for all resources in a migration plan.

        Returns {terraform_resource: complexity_dict} — used by Agent 01
        to enrich the migration plan with graph-derived complexity signals.
        """
        result: dict[str, dict] = {}
        for resource in migration_plan.get("resources", []):
            if resource.get("strategy") in ("RETIRE", "RETAIN"):
                continue
            target = resource.get("terraform_resource") or resource.get("target_service", "")
            if target:
                result[target] = self.get_migration_complexity(target)
        return result

    def rebuild(self) -> None:
        """Ré-initialise le singleton (appeler après RAGBuilder.build_all())."""
        TerraformGraphRAG._instance = None
        logger.info("GraphRAG: singleton reset — next call rebuilds from PostgreSQL")

    def graph_to_vis(self, provider: str | None = None, limit: int = 120) -> dict:
        """Export the knowledge graph as nodes + edges for frontend visualization.

        Returns a Cytoscape/D3-compatible structure:
        {
          "nodes": [{"id": str, "provider": str, "label": str, "group": str}, ...],
          "edges": [{"source": str, "target": str, "relation": str}, ...],
          "stats": {"total_nodes": int, "total_edges": int, "providers": {...}}
        }

        Args:
            provider: filter to one cloud ('aws' | 'google' | 'azurerm') or None for all
            limit:    max nodes to return (for browser performance)
        """
        nodes: list[dict] = []
        edges: list[dict] = []
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    if provider:
                        cur.execute(
                            """
                            SELECT tr.id, tr.provider, tr.description,
                                   rc.community_id, rc.community_label
                            FROM terraform_resources tr
                            LEFT JOIN resource_communities rc ON rc.resource_name = tr.id
                            WHERE tr.provider = %s
                            LIMIT %s
                            """,
                            (provider, limit),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT tr.id, tr.provider, tr.description,
                                   rc.community_id, rc.community_label
                            FROM terraform_resources tr
                            LEFT JOIN resource_communities rc ON rc.resource_name = tr.id
                            LIMIT %s
                            """,
                            (limit,),
                        )
                    for row in cur.fetchall():
                        rid, prov, desc = row[0], row[1], (row[2] or "")
                        parts = rid.split("_")
                        group = parts[1] if len(parts) > 2 else parts[-1]
                        nodes.append({
                            "id": rid,
                            "provider": prov,
                            "label": rid,
                            "group": group,
                            "description": desc[:120],
                            "community_id": row[3],
                            "community_label": row[4],
                        })

                    node_ids = {n["id"] for n in nodes}
                    cur.execute(
                        "SELECT source, target, relation FROM resource_relations "
                        "WHERE source = ANY(%s) AND target = ANY(%s)",
                        (list(node_ids), list(node_ids)),
                    )
                    for row in cur.fetchall():
                        edges.append({"source": row[0], "target": row[1], "relation": row[2]})

        except Exception as e:
            logger.warning(f"graph_to_vis failed: {e}")

        stats = self.get_graph_stats()
        return {"nodes": nodes, "edges": edges, "stats": stats}

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _fetch_meta(self, resource_name: str, provider: str) -> Optional[dict]:
        """
        Récupère les métadonnées d'une ressource depuis PostgreSQL.
        3 étapes progressives (du plus précis au plus souple) :
          Étape 1 : SELECT WHERE id = resource_name  (lookup exact)
          Étape 2 : SELECT WHERE provider = provider ORDER BY embedding <=>  (filtre + vecteur)
          Étape 3 : fallback sémantique pur (sans filtre provider)
        """
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:

                    # Étape 1 : lookup exact par ID
                    cur.execute(
                        """
                        SELECT provider, description, required_args,
                               optional_args, blocks, example
                        FROM terraform_resources
                        WHERE id = %s
                        """,
                        (resource_name,),
                    )
                    row = cur.fetchone()
                    if row:
                        return self._row_to_meta(row)

                    # Étape 2 : recherche vectorielle filtrée par provider
                    query_text = f"{resource_name} terraform {provider} resource"
                    embedding = self.embedder.encode([query_text]).tolist()[0]

                    cur.execute(
                        """
                        SELECT provider, description, required_args,
                               optional_args, blocks, example,
                               embedding <=> %s::vector AS dist
                        FROM terraform_resources
                        WHERE provider = %s
                        ORDER BY dist
                        LIMIT 5
                        """,
                        (embedding, provider),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return self._row_to_meta(rows[0])  # premier = plus proche

                    # Étape 3 : fallback sémantique pur (tous providers)
                    cur.execute(
                        """
                        SELECT provider, description, required_args,
                               optional_args, blocks, example,
                               embedding <=> %s::vector AS dist
                        FROM terraform_resources
                        ORDER BY dist
                        LIMIT 1
                        """,
                        (embedding,),
                    )
                    row = cur.fetchone()
                    if row:
                        return self._row_to_meta(row)

        except Exception as e:
            logger.warning(f"GraphRAG._fetch_meta({resource_name}) failed: {e}")

        return None

    def _row_to_meta(self, row: tuple) -> dict:
        """Convertit une ligne SQL en dict de métadonnées (format graph_rag)."""
        # Colonnes : provider, description, required_args, optional_args, blocks, example [, dist]
        return {
            "provider": row[0],
            "description": row[1] or "",
            # JSONB est déjà désérialisé par psycopg2 → json.dumps pour rester
            # compatible avec _format_single() qui appelle json.loads()
            "required_args": json.dumps(row[2]) if not isinstance(row[2], str) else row[2],
            "optional_args": json.dumps(row[3]) if not isinstance(row[3], str) else row[3],
            "blocks": json.dumps(row[4]) if not isinstance(row[4], str) else row[4],
            "example": row[5] or "",
        }

    def _multi_hop_traversal(self, resource_name: str, max_hops: int = 2) -> list[dict]:
        """Multi-hop graph traversal — uses Neo4j Cypher when available, SQL fallback otherwise.

        Returns a list of dicts: {resource, relation, via, depth}
        """
        if neo4j_available():
            return self._multi_hop_cypher(resource_name, max_hops)
        return self._multi_hop_sql(resource_name, max_hops)

    def _multi_hop_cypher(self, resource_name: str, max_hops: int = 2) -> list[dict]:
        """Neo4j Cypher traversal (Nekrasov et al. 2025 — graph path queries).

        Traverses COMPANION, DEPENDS_ON, REFERENCES, RELATED_TO edges up to max_hops.
        Also retrieves required arguments for each related resource so the LLM
        knows which fields it must set on dependency resources.
        """
        try:
            drv = get_neo4j_driver()
            with drv.session() as session:
                # Cypher does not allow a parameter ($hops) as a variable-length range bound.
                # The range literal *1..N must be a compile-time constant, so we interpolate
                # max_hops directly into the query string (it is always a small trusted integer).
                cypher = f"""
                    MATCH path = (start:Resource {{name: $name}})
                          -[:COMPANION|DEPENDS_ON|REFERENCES|RELATED_TO*1..{max_hops}]->(related:Resource)
                    WITH related,
                         relationships(path)[0] AS first_rel,
                         nodes(path)[-2]        AS via_node,
                         length(path)           AS depth
                    OPTIONAL MATCH (related)-[:HAS_ARGUMENT]->(req:Argument {{is_required: true}})
                    RETURN DISTINCT
                        related.name        AS resource,
                        type(first_rel)     AS relation,
                        via_node.name       AS via,
                        depth,
                        collect(req.arg_name) AS required_args
                    ORDER BY depth ASC
                    LIMIT 60
                """
                result = session.run(cypher, name=resource_name)
                rows = [
                    {
                        "resource":      rec["resource"],
                        "relation":      rec["relation"],
                        "via":           rec["via"],
                        "depth":         rec["depth"],
                        "required_args": list(rec["required_args"] or []),
                    }
                    for rec in result
                ]
            drv.close()
            return rows
        except Exception as e:
            logger.warning(f"_multi_hop_cypher({resource_name}) failed, falling back to SQL: {e}")
            return self._multi_hop_sql(resource_name, max_hops)

    def _multi_hop_sql(self, resource_name: str, max_hops: int = 2) -> list[dict]:
        """PostgreSQL WITH RECURSIVE fallback (used when Neo4j is unavailable)."""
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH RECURSIVE traversal(node, relation, via, depth, path) AS (
                            SELECT
                                rr.target,
                                rr.relation,
                                rr.source,
                                1,
                                ARRAY[%s::text, rr.target]
                            FROM resource_relations rr
                            WHERE rr.source = %s
                              AND rr.relation IN ('COMPANION', 'DEPENDS_ON', 'REFERENCES')

                            UNION ALL

                            SELECT
                                rr.target,
                                rr.relation,
                                rr.source,
                                t.depth + 1,
                                t.path || rr.target
                            FROM resource_relations rr
                            INNER JOIN traversal t ON rr.source = t.node
                            WHERE t.depth < %s
                              AND rr.relation IN ('COMPANION', 'DEPENDS_ON', 'REFERENCES', 'RELATED_TO')
                              AND NOT (rr.target = ANY(t.path))
                        )
                        SELECT DISTINCT ON (node) node, relation, via, depth
                        FROM traversal
                        ORDER BY node, depth ASC
                        LIMIT 60
                        """,
                        (resource_name, resource_name, max_hops),
                    )
                    return [
                        {"resource": row[0], "relation": row[1], "via": row[2], "depth": row[3],
                         "required_args": []}
                        for row in cur.fetchall()
                    ]
        except Exception as e:
            logger.warning(f"_multi_hop_sql({resource_name}) failed: {e}")
            return []

    def get_communities(self, provider: Optional[str] = None) -> dict:
        """Return community summaries for frontend visualization.

        Returns {communities: [{id, label, size, members_sample}], total_communities}
        """
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    if provider:
                        cur.execute(
                            """
                            SELECT rc.community_id, rc.community_label,
                                   COUNT(*) AS size,
                                   (ARRAY_AGG(rc.resource_name ORDER BY rc.resource_name))[1:5] AS sample
                            FROM resource_communities rc
                            JOIN terraform_resources tr ON tr.id = rc.resource_name
                            WHERE tr.provider = %s
                            GROUP BY rc.community_id, rc.community_label
                            ORDER BY size DESC
                            LIMIT 50
                            """,
                            (provider,),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT community_id, community_label,
                                   COUNT(*) AS size,
                                   (ARRAY_AGG(resource_name ORDER BY resource_name))[1:5] AS sample
                            FROM resource_communities
                            GROUP BY community_id, community_label
                            ORDER BY size DESC
                            LIMIT 100
                            """
                        )
                    communities = [
                        {
                            "id": row[0],
                            "label": row[1] or f"community_{row[0]}",
                            "size": row[2],
                            "members_sample": list(row[3]) if row[3] else [],
                        }
                        for row in cur.fetchall()
                    ]
            return {"communities": communities, "total_communities": len(communities), "available": True}
        except Exception as e:
            logger.warning(f"get_communities failed: {e}")
            return {"communities": [], "total_communities": 0, "available": False, "reason": str(e)}

    def _get_companions(self, resource_name: str) -> list[str]:
        """
        Retourne les companions via SQL JOIN sur resource_relations.
        Fallback sur _COMPANIONS statique si la table est vide.
        """
        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT target FROM resource_relations
                        WHERE source = %s AND relation = 'COMPANION'
                        """,
                        (resource_name,),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return [r[0] for r in rows]
        except Exception as e:
            logger.warning(f"GraphRAG._get_companions({resource_name}) failed: {e}")

        # Fallback statique (resource_relations vide au premier démarrage)
        return _COMPANIONS.get(resource_name, [])

    def _format_enriched(
        self,
        resource_name: str,
        meta: dict,
        companions: list[tuple[str, dict]],
    ) -> str:
        lines = [self._format_single(resource_name, meta)]
        if companions:
            lines.append(
                "\n=== COMPANION RESOURCES (auto-included — generate these too for best-practice compliance) ==="
            )
            for comp_name, comp_meta in companions:
                lines.append(f"\n--- COMPANION: {comp_name} ---")
                lines.append(self._format_single(comp_name, comp_meta))
        return "\n".join(lines)

    def _format_single(self, resource_name: str, meta: dict) -> str:
        required_args = json.loads(meta.get("required_args", "[]"))
        optional_args = json.loads(meta.get("optional_args", "[]"))
        blocks = json.loads(meta.get("blocks", "[]"))

        lines = [
            f"RESOURCE: {resource_name}",
            f"Description: {meta.get('description', '')}",
            "",
            "REQUIRED ARGUMENTS — include ALL of these:",
        ]
        for arg in required_args:
            lines.append(f"  - {arg['name']}: {arg.get('description', '')}")

        if optional_args:
            lines.append("\nOPTIONAL ARGUMENTS:")
            for arg in optional_args:
                lines.append(f"  - {arg.get('name', '')}")

        if blocks:
            lines.append("\nAVAILABLE BLOCKS:")
            for block in blocks:
                bargs = block.get("args", [])
                suffix = f" (args: {', '.join(bargs)})" if bargs else ""
                lines.append(f"  - {block.get('name', '')}{suffix}")

        if meta.get("example"):
            lines.append(f"\nUSAGE EXAMPLE:\n{meta['example']}")

        lines.append("\nCRITICAL: Only use argument names listed above.")
        return "\n".join(lines)

    def _fallback_context(self, resource_name: str) -> str:
        return (
            f"RESOURCE: {resource_name}\n"
            f"WARNING: No documentation found for this resource.\n"
            f"Generate conservative HCL using only well-known arguments.\n"
            f"# TODO: verify arguments for {resource_name}"
        )

    # -------------------------------------------------------------------------
    # Dual GraphRAG query (added 2026-04-24)
    # Combines three orthogonal signals so Agent 02 generates Terraform that is
    # both syntactically correct (structural) and architecturally sound (patterns):
    #
    #   1. Structural dependencies — tf_resource_graph / resource_relations
    #      Hard constraints: "aws_lambda_function requires aws_iam_role"
    #   2. Semantic doc chunks — terraform_resources (vector similarity)
    #      Soft guidance: "these are the args and examples for your task"
    #   3. Canonical patterns — tf_canonical_patterns (vector + array overlap)
    #      Architecture hints: "here's a full HCL skeleton for this use case"
    # -------------------------------------------------------------------------
    def graph_rag_query(
        self,
        provider: str,
        target_resource: str,
        context_resources: list[str] | None = None,
        task_text: str | None = None,
        top_k_chunks: int = 12,
        top_k_patterns: int = 3,
    ) -> dict:
        """Dual retrieval for Agent 02.

        Args:
            provider: 'aws' | 'azurerm' | 'google'
            target_resource: the Terraform resource type being generated
            context_resources: surrounding resource types in the plan (helps
                pattern and doc retrieval stay on-topic)
            task_text: free-form description of what the LLM needs to generate;
                if None, synthesised from target_resource + context_resources
            top_k_chunks: how many doc chunks to return
            top_k_patterns: how many canonical patterns to return

        Returns:
            {
              "required_dependencies": [{target_type, relation, ...}, ...],
              "doc_chunks":            [{resource, description, example, sim}, ...],
              "canonical_patterns":    [{pattern_name, resource_types, hcl_template, sim}, ...],
            }
        """
        context_resources = context_resources or []
        if task_text is None:
            task_text = f"{target_resource} in {provider} with {' '.join(context_resources)}"

        result = {
            "required_dependencies": [],
            "doc_chunks": [],
            "canonical_patterns": [],
            "argument_nodes": [],   # Nekrasov: argument-level nodes
        }

        try:
            task_embedding = self.embedder.encode([task_text]).tolist()[0]
        except Exception as e:
            logger.warning(f"graph_rag_query: embedding failed — {e}")
            return result

        scoped_types = [target_resource] + list(context_resources)

        try:
            with get_pg_connection() as conn:
                with conn.cursor() as cur:
                    # ── 1. Structural dependencies (hard constraints) ────
                    cur.execute(
                        """
                        SELECT target, relation, source_type
                        FROM resource_relations
                        WHERE source = %s AND relation IN ('COMPANION', 'DEPENDS_ON')
                        """,
                        (target_resource,),
                    )
                    result["required_dependencies"] = [
                        {"target_type": r[0], "relation": r[1], "source": r[2]}
                        for r in cur.fetchall()
                    ]

                    # ── 2. Semantic doc chunks (scoped to relevant types) ─
                    cur.execute(
                        """
                        SELECT id, provider, description, example,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM terraform_resources
                        WHERE provider = %s
                          AND id = ANY(%s)
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (task_embedding, provider, scoped_types,
                         task_embedding, top_k_chunks),
                    )
                    rows = cur.fetchall() or []
                    # If the scoped filter produced nothing, fall back to
                    # unscoped semantic search within the provider.
                    if not rows:
                        cur.execute(
                            """
                            SELECT id, provider, description, example,
                                   1 - (embedding <=> %s::vector) AS similarity
                            FROM terraform_resources
                            WHERE provider = %s
                            ORDER BY embedding <=> %s::vector
                            LIMIT %s
                            """,
                            (task_embedding, provider, task_embedding, top_k_chunks),
                        )
                        rows = cur.fetchall() or []
                    result["doc_chunks"] = [
                        {
                            "resource": r[0],
                            "provider": r[1],
                            "description": r[2] or "",
                            "example": r[3] or "",
                            "similarity": round(float(r[4]), 4),
                        }
                        for r in rows
                    ]

                    # ── 3. Canonical patterns (array overlap + vector) ───
                    # Only runs if the tf_canonical_patterns table exists.
                    try:
                        cur.execute(
                            """
                            SELECT pattern_name, description, resource_types, hcl_template,
                                   1 - (embedding <=> %s::vector) AS similarity
                            FROM tf_canonical_patterns
                            WHERE provider = %s
                              AND resource_types && %s::text[]
                            ORDER BY embedding <=> %s::vector
                            LIMIT %s
                            """,
                            (task_embedding, provider, scoped_types,
                             task_embedding, top_k_patterns),
                        )
                        result["canonical_patterns"] = [
                            {
                                "pattern_name": r[0],
                                "description": r[1] or "",
                                "resource_types": list(r[2]) if r[2] else [],
                                "hcl_template": r[3] or "",
                                "similarity": round(float(r[4]), 4),
                            }
                            for r in cur.fetchall()
                        ]
                    except Exception as exc:
                        # Table may not exist yet (migration 002 not run).
                        logger.debug(f"tf_canonical_patterns unavailable: {exc}")

                    # ── 4. Argument-level nodes (Nekrasov Graph RAG) ─────
                    # Only runs if tf_arguments is populated (migration 004 + build_all run).
                    try:
                        cur.execute(
                            """
                            SELECT ta.arg_name, ta.description, ta.arg_type,
                                   ta.is_required, ta.resource_id,
                                   1 - (ta.embedding <=> %s::vector) AS similarity
                            FROM tf_arguments ta
                            WHERE ta.resource_id = ANY(%s)
                            ORDER BY ta.embedding <=> %s::vector
                            LIMIT 16
                            """,
                            (task_embedding, scoped_types, task_embedding),
                        )
                        result["argument_nodes"] = [
                            {
                                "arg_name": r[0],
                                "description": r[1] or "",
                                "arg_type": r[2] or "",
                                "is_required": r[3],
                                "resource_id": r[4],
                                "similarity": round(float(r[5]), 4),
                            }
                            for r in cur.fetchall()
                        ]
                    except Exception as exc:
                        logger.debug(f"tf_arguments unavailable: {exc}")

        except Exception as e:
            logger.warning(f"graph_rag_query failed: {e}")

        return result
