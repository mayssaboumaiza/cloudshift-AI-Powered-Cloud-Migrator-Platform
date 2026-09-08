"""024 — Add rag_messages table for persistent RAG chat history

Each row is one message (user or assistant) tied to a migration.
The frontend loads this on mount and appends after each exchange.

Revision ID: 024_add_rag_messages
Revises: 023_add_user_id_to_migrations
Create Date: 2026-06-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from db_utils.schema_guards import _index_exists

revision: str = "024_add_rag_messages"
down_revision: Union[str, Sequence[str], None] = "023_add_user_id_to_migrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if "rag_messages" not in existing:
        op.create_table(
            "rag_messages",
            sa.Column("id",           sa.Integer(),                  primary_key=True, autoincrement=True),
            sa.Column("migration_id", sa.String(36),                 nullable=False),
            sa.Column("role",         sa.String(20),                 nullable=False),   # "user" | "assistant"
            sa.Column("content",      sa.Text(),                     nullable=False),
            sa.Column("sources_count",sa.Integer(),                  nullable=False, server_default="0"),
            sa.Column("created_at",   sa.DateTime(timezone=True),    nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["migration_id"], ["migrations.id"], ondelete="CASCADE"),
        )
        conn = op.get_bind()
        if not _index_exists(conn, "ix_rag_messages_migration_id"):
            op.create_index("ix_rag_messages_migration_id", "rag_messages", ["migration_id"])
        if not _index_exists(conn, "ix_rag_messages_created_at"):
            op.create_index("ix_rag_messages_created_at", "rag_messages", ["created_at"])


def downgrade() -> None:
    op.drop_table("rag_messages")
