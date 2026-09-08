"""Consolidate 8 tables → 4 tables.

Changes:
1. CREATE pipeline_state (replaces checkpoint_blobs + workflow_events)
2. ADD migrations.migration_priority JSON column (replaces migration_priorities table)
3. DROP checkpoint_blobs
4. DROP checkpoint_edges
5. DROP workflow_events
6. DROP migration_priorities
7. DROP service_inventories (already deprecated since 002, now formally removed)

Revision ID: 007_consolidate_tables
Revises: 006_add_repo_urls
Create Date: 2026-05-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from db_utils.schema_guards import _index_exists

revision: str = "007_consolidate_tables"
down_revision: Union[str, Sequence[str], None] = "006_add_repo_urls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── 1. Create pipeline_state ─────────────────────────────────────────────
    if "pipeline_state" not in existing_tables:
        op.create_table(
            "pipeline_state",
            sa.Column("thread_id",      sa.String(36),  primary_key=True, nullable=False),
            sa.Column("checkpoint_id",  sa.String(100), nullable=False, unique=True),
            sa.Column("checkpoint",     sa.JSON(),      nullable=False),
            sa.Column("meta_info",      sa.JSON(),      nullable=True),
            sa.Column("events",         sa.JSON(),      nullable=False, server_default="[]"),
            sa.Column("created_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at",     sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        conn = op.get_bind()
        if not _index_exists(conn, "ix_pipeline_state_thread_id"):
            op.create_index("ix_pipeline_state_thread_id",    "pipeline_state", ["thread_id"])
        if not _index_exists(conn, "ix_pipeline_state_checkpoint_id"):
            op.create_index("ix_pipeline_state_checkpoint_id","pipeline_state", ["checkpoint_id"], unique=True)
        if not _index_exists(conn, "ix_pipeline_state_created_at"):
            op.create_index("ix_pipeline_state_created_at",   "pipeline_state", ["created_at"])

        # Migrate existing checkpoint_blobs rows into pipeline_state
        if "checkpoint_blobs" in existing_tables:
            op.execute("""
                INSERT INTO pipeline_state (thread_id, checkpoint_id, checkpoint, meta_info, events, created_at, updated_at)
                SELECT thread_id, checkpoint_id, checkpoint, meta_info, '[]'::json, created_at, updated_at
                FROM checkpoint_blobs
                ON CONFLICT (thread_id) DO NOTHING
            """)

    # ── 2. Add migration_priority column to migrations ───────────────────────
    # Guard: skip on a fresh DB where the migrations table doesn't exist yet.
    existing_cols = (
        {c["name"] for c in inspector.get_columns("migrations")}
        if "migrations" in existing_tables else set()
    )
    if "migrations" in existing_tables and "migration_priority" not in existing_cols:
        op.add_column("migrations", sa.Column("migration_priority", sa.JSON(), nullable=True))

        # Migrate existing migration_priorities rows into migrations.migration_priority
        if "migration_priorities" in existing_tables:
            op.execute("""
                UPDATE migrations m
                SET migration_priority = sub.priority_json
                FROM (
                    SELECT migration_id,
                           json_agg(
                               json_build_object(
                                   'priority_order',     priority_order,
                                   'service_id',         service_id,
                                   'service_name',       service_name,
                                   'phase',              phase,
                                   'dependencies_count', dependencies_count
                               ) ORDER BY priority_order
                           ) AS priority_json
                    FROM migration_priorities
                    GROUP BY migration_id
                ) sub
                WHERE m.id = sub.migration_id
            """)

    # ── 3. Drop obsolete tables ──────────────────────────────────────────────
    for table in ("checkpoint_blobs", "checkpoint_edges", "workflow_events",
                  "migration_priorities", "service_inventories"):
        if table in existing_tables:
            op.drop_table(table)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # Restore checkpoint_blobs from pipeline_state
    if "checkpoint_blobs" not in existing_tables and "pipeline_state" in existing_tables:
        op.create_table(
            "checkpoint_blobs",
            sa.Column("thread_id",     sa.String(36),  primary_key=True, nullable=False),
            sa.Column("checkpoint_id", sa.String(100), nullable=False, unique=True),
            sa.Column("checkpoint",    sa.JSON(),       nullable=False),
            sa.Column("meta_info",     sa.JSON(),       nullable=True),
            sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at",    sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.execute("""
            INSERT INTO checkpoint_blobs (thread_id, checkpoint_id, checkpoint, meta_info, created_at, updated_at)
            SELECT thread_id, checkpoint_id, checkpoint, meta_info, created_at, updated_at
            FROM pipeline_state
            ON CONFLICT (thread_id) DO NOTHING
        """)

    # Restore migration_priorities
    if "migration_priorities" not in existing_tables:
        op.create_table(
            "migration_priorities",
            sa.Column("id",                  sa.String(36),  primary_key=True),
            sa.Column("migration_id",        sa.String(36),  nullable=False),
            sa.Column("service_id",          sa.String(100), nullable=False),
            sa.Column("service_name",        sa.String(100), nullable=False),
            sa.Column("priority_order",      sa.Integer(),   nullable=False),
            sa.Column("phase",               sa.String(50),  nullable=True),
            sa.Column("dependencies_count",  sa.Integer(),   default=0),
            sa.Column("created_at",          sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    # Drop pipeline_state and migration_priority column
    if "pipeline_state" in existing_tables:
        op.drop_table("pipeline_state")

    if "migrations" in existing_tables:
        existing_cols = {c["name"] for c in inspector.get_columns("migrations")}
        if "migration_priority" in existing_cols:
            op.drop_column("migrations", "migration_priority")
