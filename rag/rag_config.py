import os
import threading
from contextlib import contextmanager
from pathlib import Path
from dotenv import load_dotenv

RAG_DIR = Path(__file__).parent

_PROJECT_ROOT = RAG_DIR.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=False)
DATA_DIR = RAG_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")

# Kept for backward compatibility — fetcher.py still reads/writes this file
# as a fallback when the rag_commits SQL table is not yet populated.
COMMITS_FILE = DATA_DIR / "last_commits.json"

# ---------------------------------------------------------------------------
# PostgreSQL connection pool
# Uses the same DB_* env vars as the main application (configuration/settings.py).
#
# FIX: was SimpleConnectionPool(maxconn=5) with connections never returned.
# Now: ThreadedConnectionPool(maxconn=20) + proper context manager via
# get_pg_connection() that calls putconn() on exit.
# ---------------------------------------------------------------------------
_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pool():
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                import psycopg2.pool
                _pg_pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=2,
                    maxconn=int(os.getenv("DB_POOL_RAG_MAX", "20")),
                    host=os.getenv("DB_HOST", "localhost"),
                    port=int(os.getenv("DB_PORT", "5432")),
                    user=os.getenv("DB_USER", "postgres"),
                    password=os.getenv("DB_PASSWORD", ""),
                    dbname=os.getenv("DB_NAME", "cloud_migrator"),
                )
    return _pg_pool


@contextmanager
def get_pg_connection():
    """Context manager: borrow a connection from the pool, commit/rollback, return it."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


def borrow_pg_connection():
    """Borrow a raw connection (for callers that manage scope manually).
    MUST call return_pg_connection() when done — or connections will leak."""
    return _get_pool().getconn()


def return_pg_connection(conn):
    """Return a raw borrowed connection back to the pool."""
    _get_pool().putconn(conn)


# ---------------------------------------------------------------------------
# Neo4j connection
# Used by rag/neo4j_sync.py and graph_rag.py for Cypher-based graph traversal.
# Falls back gracefully to SQL if NEO4J_URI is not set.
# ---------------------------------------------------------------------------
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")


def get_neo4j_driver():
    """Return a neo4j.GraphDatabase driver. Caller must close() it."""
    from neo4j import GraphDatabase
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def neo4j_available() -> bool:
    """Return True if Neo4j is reachable, False otherwise (used for graceful fallback)."""
    try:
        drv = get_neo4j_driver()
        drv.verify_connectivity()
        drv.close()
        return True
    except Exception:
        return False


PROVIDERS = {
    "aws": {
        "repo": "hashicorp/terraform-provider-aws",
        "docs_path": "website/docs/r",
        "examples_path": "examples",
        "resource_prefix": "aws_",
        "branch": "main",
    },
    "azurerm": {
        "repo": "hashicorp/terraform-provider-azurerm",
        "docs_path": "website/docs/r",
        "resource_prefix": "azurerm_",
        "branch": "main",
    },
    "google": {
        "repo": "hashicorp/terraform-provider-google",
        "docs_path": "website/docs/r",
        "resource_prefix": "google_",
        "branch": "main",
    },
}

GITHUB_API_BASE = "https://api.github.com"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
