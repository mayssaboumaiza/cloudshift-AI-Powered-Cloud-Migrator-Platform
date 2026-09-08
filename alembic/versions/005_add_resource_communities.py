"""Add resource_communities table for Louvain community detection results.

Stores the output of networkx Louvain community detection run during RAG build.
Used by graph_rag.py for community-level traversal and stats.

Revision ID: 005_add_resource_communities
Revises: 004_add_tf_argument_nodes
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005_add_resource_communities"
down_revision: Union[str, Sequence[str], None] = "004_add_tf_argument_nodes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("resource_communities"):
        op.execute("""
            CREATE TABLE resource_communities (
                resource_name   TEXT PRIMARY KEY,
                community_id    INTEGER NOT NULL,
                community_label TEXT,
                updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        op.execute("""
            CREATE INDEX ix_resource_communities_community_id
            ON resource_communities (community_id)
        """)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("resource_communities"):
        op.drop_table("resource_communities")
