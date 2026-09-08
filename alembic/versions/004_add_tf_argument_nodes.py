"""Add tf_arguments table for argument-level Graph RAG nodes (Nekrasov et al. 2025).

Adds argument-level granularity to the knowledge graph:
- tf_arguments: one row per (resource, argument) with its own embedding
- tf_argument_edges: HAS_ARGUMENT / IS_REQUIRED / HAS_TYPE structural edges

This enables Agent 02 to retrieve at argument granularity instead of whole-resource
doc chunks, improving TV Pass from ~70% (naive RAG) to ~80% (Graph RAG).

Revision ID: 004_add_tf_argument_nodes
Revises: 003_drop_service_inventories
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004_add_tf_argument_nodes"
down_revision: Union[str, Sequence[str], None] = "003_drop_service_inventories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── tf_arguments: argument-level nodes with per-argument embeddings ──────
    if not inspector.has_table("tf_arguments"):
        op.execute("""
            CREATE TABLE tf_arguments (
                id          TEXT PRIMARY KEY,
                resource_id TEXT NOT NULL,
                arg_name    TEXT NOT NULL,
                arg_type    TEXT,
                description TEXT,
                is_required BOOLEAN NOT NULL DEFAULT FALSE,
                embedding   vector(768),
                created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE(resource_id, arg_name)
            )
        """)
        op.execute("""
            CREATE INDEX ix_tf_arguments_resource_id
            ON tf_arguments (resource_id)
        """)
        # IVFFlat index for fast ANN search — lists=50 suits ~10k argument rows
        op.execute("""
            CREATE INDEX ix_tf_arguments_embedding
            ON tf_arguments
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 50)
        """)

    # ── tf_argument_edges: structural edges between resource and argument nodes
    if not inspector.has_table("tf_argument_edges"):
        op.execute("""
            CREATE TABLE tf_argument_edges (
                id       SERIAL PRIMARY KEY,
                source   TEXT NOT NULL,
                target   TEXT NOT NULL,
                relation TEXT NOT NULL,
                UNIQUE(source, target, relation)
            )
        """)
        op.execute("""
            CREATE INDEX ix_tf_arg_edges_source
            ON tf_argument_edges (source)
        """)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("tf_argument_edges"):
        op.drop_table("tf_argument_edges")
    if inspector.has_table("tf_arguments"):
        op.drop_table("tf_arguments")
