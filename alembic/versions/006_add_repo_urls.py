"""Add repo_urls JSON column for multi-repo migration support.

Revision ID: 006_add_repo_urls
Revises: 005_add_resource_communities
Create Date: 2026-05-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _table_exists, _column_exists

revision: str = "006_add_repo_urls"
down_revision: Union[str, Sequence[str], None] = "005_add_resource_communities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Guard: skip on a fresh DB where create_all hasn't run yet.
    if _table_exists(conn, "migrations") and not _column_exists(conn, "migrations", "repo_urls"):
        op.add_column(
            "migrations",
            sa.Column("repo_urls", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _table_exists(conn, "migrations") and _column_exists(conn, "migrations", "repo_urls"):
        op.drop_column("migrations", "repo_urls")
