"""
_build_relations_offline.py — One-shot: peuple resource_relations depuis les
ressources DEJA en base (sans téléchargement GitHub), puis synchronise Neo4j.

Réutilise RAGBuilder._build_graph_relations + _build_communities en
reconstruisant des objets 'resource' légers à partir de terraform_resources
(colonnes example + description servent de raw_content pour les parsers).

Usage (dans le conteneur API):
    python -m rag._build_relations_offline
"""
import sys
from types import SimpleNamespace

from rag.rag_config import get_pg_connection
from rag.corpus_builder import RAGBuilder, PROVIDERS


def _load_resources(provider: str) -> dict:
    """Retourne {resource_id: SimpleNamespace(resource_name, raw_content, ...)}."""
    prefix = PROVIDERS[provider]["resource_prefix"]
    out = {}
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, COALESCE(example,''), COALESCE(description,'')
                FROM terraform_resources
                WHERE id LIKE %s
                """,
                (prefix + "%",),
            )
            for rid, example, description in cur.fetchall():
                out[rid] = SimpleNamespace(
                    resource_name=rid,
                    raw_content=(example + "\n\n" + description),
                    example=example,
                    description=description,
                )
    return out


def main() -> int:
    builder = RAGBuilder(github_token=None)
    grand_total_edges = 0
    for provider in PROVIDERS:
        resources = _load_resources(provider)
        print(f"[{provider}] {len(resources)} ressources chargées depuis la DB")
        if not resources:
            continue
        builder._build_graph_relations(provider, resources)
        try:
            builder._build_communities(provider, resources)
        except Exception as e:
            print(f"[{provider}] communities skipped: {e}")

    # Compte final des relations en base
    with get_pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM resource_relations")
            grand_total_edges = cur.fetchone()[0]
    print(f"\nresource_relations contient maintenant {grand_total_edges} arêtes")

    # Sync vers Neo4j
    builder._sync_neo4j()
    return 0


if __name__ == "__main__":
    sys.exit(main())
