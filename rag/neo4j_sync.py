"""
neo4j_sync.py — Synchronise le graphe Terraform de PostgreSQL vers Neo4j.

Schéma Neo4j (Nekrasov et al. 2025) :
  (:Resource {name, provider, description, example})
    -[:DEPENDS_ON]->(:Resource)
    -[:COMPANION]->(:Resource)
    -[:REFERENCES]->(:Resource)
    -[:RELATED_TO]->(:Resource)
  (:Resource)-[:HAS_ARGUMENT]->(:Argument {name, description, arg_type, is_required})
  (:Resource)-[:IN_COMMUNITY]->(:Community {id, label})

Public API :
    sync = Neo4jSync()
    sync.sync_all()          # sync complète PG → Neo4j
    sync.clear()             # vide le graphe Neo4j (avant re-sync)
    sync.stats()             # retourne {nodes, edges}
"""

import logging
from rag.rag_config import get_neo4j_driver, get_pg_connection

logger = logging.getLogger("Neo4jSync")

# Taille des batches pour les requêtes UNWIND (performance)
_BATCH = 500


class Neo4jSync:
    """Synchronise le graphe RAG de PostgreSQL vers Neo4j."""

    def __init__(self):
        self.driver = get_neo4j_driver()

    def close(self):
        self.driver.close()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def sync_all(self) -> dict:
        """Sync complète : ressources + relations + arguments + communautés.

        Returns: {"resources": int, "relations": int, "arguments": int, "communities": int}
        """
        print("Neo4j sync: creating constraints and indexes...")
        self._create_schema()

        print("Neo4j sync: syncing resources...")
        n_resources = self._sync_resources()
        print(f"  → {n_resources} Resource nodes upserted")

        print("Neo4j sync: syncing relations...")
        n_relations = self._sync_relations()
        print(f"  → {n_relations} relation edges upserted")

        print("Neo4j sync: syncing arguments...")
        n_args = self._sync_arguments()
        print(f"  → {n_args} Argument nodes upserted")

        print("Neo4j sync: syncing communities...")
        n_comm = self._sync_communities()
        print(f"  → {n_comm} Community nodes upserted")

        return {
            "resources": n_resources,
            "relations": n_relations,
            "arguments": n_args,
            "communities": n_comm,
        }

    def clear(self):
        """Vide complètement le graphe Neo4j (DETACH DELETE tout)."""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.info("Neo4j graph cleared")

    def stats(self) -> dict:
        """Retourne {nodes, edges, resources, arguments, communities}."""
        with self.driver.session() as session:
            nodes      = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            edges      = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            resources  = session.run("MATCH (r:Resource) RETURN count(r) AS c").single()["c"]
            arguments  = session.run("MATCH (a:Argument) RETURN count(a) AS c").single()["c"]
            communities = session.run("MATCH (c:Community) RETURN count(c) AS c").single()["c"]
        return {
            "nodes": nodes,
            "edges": edges,
            "resources": resources,
            "arguments": arguments,
            "communities": communities,
        }

    # -------------------------------------------------------------------------
    # Schema (contraintes + index)
    # -------------------------------------------------------------------------

    def _create_schema(self):
        stmts = [
            "CREATE CONSTRAINT resource_name IF NOT EXISTS FOR (r:Resource) REQUIRE r.name IS UNIQUE",
            "CREATE CONSTRAINT argument_key IF NOT EXISTS FOR (a:Argument) REQUIRE (a.resource_name, a.arg_name) IS NODE KEY",
            "CREATE CONSTRAINT community_id IF NOT EXISTS FOR (c:Community) REQUIRE c.community_id IS UNIQUE",
            "CREATE INDEX resource_provider IF NOT EXISTS FOR (r:Resource) ON (r.provider)",
        ]
        with self.driver.session() as session:
            for stmt in stmts:
                try:
                    session.run(stmt)
                except Exception as e:
                    logger.debug(f"Schema stmt skipped ({e}): {stmt[:60]}")

    # -------------------------------------------------------------------------
    # Sync ressources (terraform_resources → :Resource nodes)
    # -------------------------------------------------------------------------

    def _sync_resources(self) -> int:
        rows = self._pg_fetch(
            """
            SELECT id, provider, description, example,
                   required_args, optional_args
            FROM terraform_resources
            """
        )
        total = 0
        for batch in _chunks(rows, _BATCH):
            params = [
                {
                    "name":        r[0],
                    "provider":    r[1],
                    "description": (r[2] or "")[:500],
                    "example":     (r[3] or "")[:1000],
                    # Sérialise les listes d'args comme chaîne JSON lisible par le LLM
                    "required_args": _args_summary(r[4]),
                    "optional_args": _args_summary(r[5]),
                }
                for r in batch
            ]
            with self.driver.session() as session:
                session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (r:Resource {name: row.name})
                    SET r.provider    = row.provider,
                        r.description = row.description,
                        r.example     = row.example,
                        r.required_args = row.required_args,
                        r.optional_args = row.optional_args
                    """,
                    rows=params,
                )
            total += len(batch)
        return total

    # -------------------------------------------------------------------------
    # Sync relations (resource_relations → edges entre :Resource)
    # -------------------------------------------------------------------------

    def _sync_relations(self) -> int:
        rows = self._pg_fetch(
            "SELECT source, target, relation FROM resource_relations"
        )
        total = 0
        # Groupe par type de relation pour utiliser des clauses Cypher dynamiques
        rel_types: dict[str, list] = {}
        for source, target, rel in rows:
            rel_types.setdefault(rel, []).append({"source": source, "target": target})

        for rel_type, pairs in rel_types.items():
            # Filtre les types autorisés pour éviter l'injection Cypher
            if rel_type not in {"COMPANION", "DEPENDS_ON", "REFERENCES", "RELATED_TO"}:
                continue
            for batch in _chunks(pairs, _BATCH):
                with self.driver.session() as session:
                    session.run(
                        f"""
                        UNWIND $rows AS row
                        MATCH (src:Resource {{name: row.source}})
                        MATCH (tgt:Resource {{name: row.target}})
                        MERGE (src)-[:{rel_type}]->(tgt)
                        """,
                        rows=batch,
                    )
                total += len(batch)
        return total

    # -------------------------------------------------------------------------
    # Sync arguments (tf_arguments → :Argument nodes + HAS_ARGUMENT edges)
    # -------------------------------------------------------------------------

    def _sync_arguments(self) -> int:
        try:
            rows = self._pg_fetch(
                """
                SELECT resource_id, arg_name, description, arg_type, is_required
                FROM tf_arguments
                """
            )
        except Exception:
            logger.debug("tf_arguments table not available — skipping argument sync")
            return 0

        total = 0
        for batch in _chunks(rows, _BATCH):
            params = [
                {
                    "resource_name": r[0],
                    "arg_name":      r[1],
                    "description":   (r[2] or "")[:300],
                    "arg_type":      r[3] or "",
                    "is_required":   bool(r[4]),
                }
                for r in batch
            ]
            with self.driver.session() as session:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (res:Resource {name: row.resource_name})
                    MERGE (arg:Argument {resource_name: row.resource_name, arg_name: row.arg_name})
                    SET arg.description = row.description,
                        arg.arg_type    = row.arg_type,
                        arg.is_required = row.is_required
                    MERGE (res)-[:HAS_ARGUMENT]->(arg)
                    """,
                    rows=params,
                )
            total += len(batch)
        return total

    # -------------------------------------------------------------------------
    # Sync communautés (resource_communities → :Community nodes + IN_COMMUNITY)
    # -------------------------------------------------------------------------

    def _sync_communities(self) -> int:
        try:
            rows = self._pg_fetch(
                "SELECT resource_name, community_id, community_label FROM resource_communities"
            )
        except Exception:
            logger.debug("resource_communities table not available — skipping")
            return 0

        total = 0
        for batch in _chunks(rows, _BATCH):
            params = [
                {
                    "resource_name":   r[0],
                    "community_id":    r[1],
                    "community_label": r[2] or "",
                }
                for r in batch
            ]
            with self.driver.session() as session:
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (res:Resource {name: row.resource_name})
                    MERGE (c:Community {community_id: row.community_id})
                    SET c.label = row.community_label
                    MERGE (res)-[:IN_COMMUNITY]->(c)
                    """,
                    rows=params,
                )
            total += len(batch)
        return total

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _pg_fetch(query: str) -> list:
        with get_pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return cur.fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers module-level
# ─────────────────────────────────────────────────────────────────────────────

def _chunks(lst: list, n: int):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def _args_summary(args_json) -> str:
    """Sérialise la liste d'arguments en texte lisible (stocké dans Neo4j)."""
    if not args_json:
        return ""
    if isinstance(args_json, str):
        import json
        try:
            args_json = json.loads(args_json)
        except Exception:
            return args_json
    parts = []
    for a in args_json[:20]:  # max 20 args pour limiter la taille
        if isinstance(a, dict) and a.get("name"):
            parts.append(a["name"])
    return ", ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# CLI : python -m rag.neo4j_sync
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    force_clear = "--clear" in sys.argv

    sync = Neo4jSync()
    try:
        if force_clear:
            print("Clearing Neo4j graph...")
            sync.clear()

        result = sync.sync_all()
        print(f"\nSync complete: {result}")

        s = sync.stats()
        print(f"Neo4j stats: {s['resources']} resources, {s['arguments']} arguments, "
              f"{s['communities']} communities, {s['edges']} edges total")
    finally:
        sync.close()
