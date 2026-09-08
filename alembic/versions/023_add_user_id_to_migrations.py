"""023 — Add user_id FK to migrations (multi-tenancy phase 1)

Nullable for backward-compat: existing rows keep user_id=NULL and remain
accessible to admins only until back-filled.
A NOT NULL constraint is added in a follow-up migration (023b) once all
rows have been associated to a user.

Design choices:
- Column is nullable so existing rows are not broken.
- FK uses ON DELETE SET NULL so deleting a user does not cascade-delete migrations.
- The upgrade function is fully idempotent: it checks for column and index existence
  via pg_attribute / pg_indexes before issuing DDL, so it is safe to run on a DB
  that was partially migrated by hand or via a previous failed run.

Revision ID: 023_add_user_id_to_migrations
Revises: 022_add_invitations_table
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = "023_add_user_id_to_migrations"
down_revision = "022_add_invitations_table"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).fetchone()
    return row is not None


def _index_exists(conn, index: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM pg_indexes WHERE indexname = :i"
        ),
        {"i": index},
    ).fetchone()
    return row is not None


def _fk_exists(conn, constraint: str) -> bool:
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.table_constraints "
            "WHERE constraint_name = :c AND constraint_type = 'FOREIGN KEY'"
        ),
        {"c": constraint},
    ).fetchone()
    return row is not None


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, "migrations", "user_id"):
        op.add_column(
            "migrations",
            sa.Column("user_id", sa.String(36), nullable=True),
        )

    if not _fk_exists(conn, "migrations_user_id_fkey"):
        op.create_foreign_key(
            "migrations_user_id_fkey",
            "migrations",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not _index_exists(conn, "ix_migrations_user_id"):
        op.create_index("ix_migrations_user_id", "migrations", ["user_id"])


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "ix_migrations_user_id"):
        op.drop_index("ix_migrations_user_id", table_name="migrations")
    if _fk_exists(conn, "migrations_user_id_fkey"):
        op.drop_constraint("migrations_user_id_fkey", "migrations", type_="foreignkey")
    if _column_exists(conn, "migrations", "user_id"):
        op.drop_column("migrations", "user_id")
