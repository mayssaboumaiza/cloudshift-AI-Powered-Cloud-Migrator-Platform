"""
alembic/_schema_utils.py — Shared DDL guard utilities for all Alembic migrations.

Every public function takes a live DBAPI connection (op.get_bind()) and returns
a bool so migrations can gate their DDL with a simple ``if not _xxx_exists(...):``.

Rules enforced here:
  - All queries use information_schema or pg_catalog — no SQLAlchemy Inspector
    (Inspector caches its reflection; these functions always hit the live DB).
  - All functions are read-only. They never mutate schema.
  - Thread-safe: each call is a single parameterised SELECT, no transaction state.

Usage in a migration::

    from alembic._schema_utils import (
        _table_exists, _column_exists, _index_exists,
        _constraint_exists, _type_exists,
    )

    def upgrade() -> None:
        conn = op.get_bind()
        if not _table_exists(conn, "my_table"):
            op.create_table(...)
        if not _column_exists(conn, "my_table", "new_col"):
            op.add_column(...)
        if not _index_exists(conn, "ix_my_table_col"):
            op.create_index(...)
        if not _constraint_exists(conn, "fk_my_table_other"):
            op.create_foreign_key(...)
"""
from __future__ import annotations

from sqlalchemy.engine import Connection
from sqlalchemy import text


# ── Tables ────────────────────────────────────────────────────────────────────

def _table_exists(conn: Connection, table: str, schema: str = "public") -> bool:
    """Return True if *table* exists in *schema*."""
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = :t"
        ),
        {"s": schema, "t": table},
    ).fetchone()
    return row is not None


# ── Columns ───────────────────────────────────────────────────────────────────

def _column_exists(conn: Connection, table: str, column: str, schema: str = "public") -> bool:
    """Return True if *column* exists on *table*."""
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = :t AND column_name = :c"
        ),
        {"s": schema, "t": table, "c": column},
    ).fetchone()
    return row is not None


# ── Indexes ───────────────────────────────────────────────────────────────────

def _index_exists(conn: Connection, index_name: str, schema: str = "public") -> bool:
    """Return True if an index named *index_name* exists in *schema*."""
    row = conn.execute(
        text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = :s AND indexname = :i"
        ),
        {"s": schema, "i": index_name},
    ).fetchone()
    return row is not None


# ── Constraints (FK, UNIQUE, CHECK, PRIMARY KEY) ──────────────────────────────

def _constraint_exists(
    conn: Connection,
    constraint_name: str,
    schema: str = "public",
) -> bool:
    """Return True if a table constraint with *constraint_name* exists."""
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE constraint_schema = :s AND constraint_name = :c"
        ),
        {"s": schema, "c": constraint_name},
    ).fetchone()
    return row is not None


# ── PostgreSQL custom types (ENUM, DOMAIN) ────────────────────────────────────

def _type_exists(conn: Connection, type_name: str, schema: str = "public") -> bool:
    """Return True if a PostgreSQL type (e.g. ENUM) named *type_name* exists."""
    row = conn.execute(
        text(
            "SELECT 1 FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = :s AND t.typname = :type"
        ),
        {"s": schema, "type": type_name},
    ).fetchone()
    return row is not None


# ── Enum values ───────────────────────────────────────────────────────────────

def _enum_has_value(conn: Connection, type_name: str, value: str) -> bool:
    """Return True if PostgreSQL ENUM *type_name* contains *value*."""
    row = conn.execute(
        text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :type AND e.enumlabel = :val"
        ),
        {"type": type_name, "val": value},
    ).fetchone()
    return row is not None
