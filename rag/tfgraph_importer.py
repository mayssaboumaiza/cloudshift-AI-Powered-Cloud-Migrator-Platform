"""
tfgraph_importer.py — Importe un graphe Terraform réel dans resource_relations.

`terraform graph` produit un format DOT (Graphviz) représentant les dépendances
réelles entre ressources dans un projet .tf. Ce module parse ce DOT et insère
les arêtes dans resource_relations avec relation='DEPENDS_ON'.

Interface publique :
    import_from_dot(dot_string, conn)        → int (arêtes insérées)
    import_from_directory(tf_dir, conn)      → int (arêtes insérées)

Règles de normalisation :
    • "aws_lambda_function.my_func"  → "aws_lambda_function"
    • Ignorer les méta-nœuds : [root], var.*, data.*, local.*, module.*
    • Ignorer les auto-boucles (source == target après normalisation)
    • ON CONFLICT DO NOTHING → idempotent, safe à relancer
"""

import re
import subprocess
from pathlib import Path
from typing import Optional

import psycopg2.extras

from rag.rag_config import get_pg_connection, borrow_pg_connection, return_pg_connection

# Préfixes des méta-nœuds Terraform à ignorer
_SKIP_PREFIXES = ("var.", "data.", "local.", "module.", "provider[")
# Nœuds racines spéciaux (entre crochets dans le DOT)
_SKIP_PATTERNS = re.compile(r"^\[.*\]$")


def _normalize(node: str) -> Optional[str]:
    """
    Normalise un nœud DOT Terraform en nom de resource_type.

    Exemples :
        "aws_lambda_function.processor" → "aws_lambda_function"
        "[root] aws_s3_bucket.data"     → "aws_s3_bucket"
        "var.region"                    → None  (ignoré)
        "[root]"                        → None  (ignoré)
    """
    # Supprimer le préfixe "[root] " ou "[module.x] "
    node = re.sub(r"^\[.*?\]\s*", "", node).strip()

    if not node:
        return None
    if _SKIP_PATTERNS.match(node):
        return None
    for prefix in _SKIP_PREFIXES:
        if node.startswith(prefix):
            return None

    # Garder uniquement le type (partie avant le premier point)
    # ex: aws_lambda_function.my_func → aws_lambda_function
    return node.split(".")[0] if "." in node else node


def _parse_dot_edges(dot_string: str) -> list[tuple[str, str]]:
    """
    Extrait les paires (source, target) depuis un DOT produit par `terraform graph`.

    Format attendu :
        digraph {
          "aws_lambda_function.fn" -> "aws_iam_role.role"
          ...
        }
    Les nœuds peuvent être entre guillemets ou non.
    """
    # Capture les arêtes : "nœud A" -> "nœud B"  (avec ou sans guillemets)
    edge_re = re.compile(
        r'"?([^";\n\r]+?)"?\s*->\s*"?([^";\n\r]+?)"?\s*(?:\[.*?\])?\s*$',
        re.MULTILINE,
    )
    edges: list[tuple[str, str]] = []
    for match in edge_re.finditer(dot_string):
        raw_src = match.group(1).strip()
        raw_tgt = match.group(2).strip()

        src = _normalize(raw_src)
        tgt = _normalize(raw_tgt)

        if src and tgt and src != tgt:  # ignorer les auto-boucles
            edges.append((src, tgt))

    return edges


def import_from_dot(
    dot_string: str,
    conn=None,
) -> int:
    """
    Parse le DOT et insère les arêtes DEPENDS_ON dans resource_relations.

    Paramètres :
        dot_string : sortie brute de `terraform graph`
        conn       : connexion psycopg2 existante (optionnel — créée si None)

    Retourne le nombre d'arêtes insérées (hors conflits ignorés).
    """
    edges = _parse_dot_edges(dot_string)
    if not edges:
        return 0

    # Dédupliquer les arêtes avant l'insertion
    unique_edges = list({(s, t) for s, t in edges})

    rows = [(s, t, "DEPENDS_ON", "tfgraph") for s, t in unique_edges]

    sql = """
        INSERT INTO resource_relations (source, target, relation, source_type)
        VALUES %s
        ON CONFLICT (source, target, relation) DO NOTHING
    """

    _own_conn = conn is None
    if _own_conn:
        conn = borrow_pg_connection()

    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, rows)
            inserted_count = cur.rowcount
        conn.commit()
        return inserted_count
    finally:
        if _own_conn:
            return_pg_connection(conn)


def import_from_directory(
    tf_dir: str,
    conn=None,
) -> int:
    """
    Lance `terraform graph` dans tf_dir et importe le résultat.

    Prérequis : terraform CLI disponible dans le PATH et le répertoire
    doit avoir été initialisé avec `terraform init`.

    Retourne le nombre d'arêtes insérées.
    """
    tf_path = Path(tf_dir).resolve()
    if not tf_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {tf_path}")

    try:
        result = subprocess.run(
            ["terraform", "graph"],
            cwd=str(tf_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "terraform CLI not found in PATH. "
            "Install Terraform or use import_from_dot() with a pre-generated DOT string."
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"terraform graph failed (exit {result.returncode}):\n{result.stderr}"
        )

    return import_from_dot(result.stdout, conn=conn)
