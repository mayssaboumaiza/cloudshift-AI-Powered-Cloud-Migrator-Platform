"""consolidate dependency schema: merge service_inventories into dependency_nodes

Adds to dependency_nodes:
  - canonical_id (VARCHAR 64, indexed) — stable ID across repos
  - repo_origin  (VARCHAR 200, indexed) — source repo slug for multi-repo platforms
  - metadata     (JSONB) — absorbs ServiceInventory (detection_frequency, detections_by)
                           + free-form slot for future growth

Adds to dependency_edges:
  - edge_type (VARCHAR 30, default 'depends_on')
  - evidence  (JSONB) — shared handle (URL/ARN/queue name) that justified
                        a cross-repo edge

Data migration:
  Merges service_inventories rows into the matching DependencyNode.metadata
  based on (migration_id, cloud_provider, service_name). The service_inventories
  table is NOT dropped in this migration to give callers a cycle to move off it.

Revision ID: 002_consolidate_dependency_schema
Revises: 001_add_artifacts_column
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002_consolidate_dependency_schema"
down_revision: Union[str, Sequence[str], None] = "001_add_artifacts_column"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ── 1. Add new columns to dependency_nodes (idempotent) ─────────────
    # Guard: on a fresh DB (ALEMBIC_MANAGED with no create_all yet) the table
    # may not exist — skip rather than crash with NoSuchTableError. create_all
    # (dev/CI) bootstraps the base schema before alembic runs.
    if inspector.has_table("dependency_nodes"):
        existing_node_cols = {c["name"] for c in inspector.get_columns("dependency_nodes")}

        if "canonical_id" not in existing_node_cols:
            op.add_column(
                "dependency_nodes",
                sa.Column("canonical_id", sa.String(64), nullable=True),
            )
        if "repo_origin" not in existing_node_cols:
            op.add_column(
                "dependency_nodes",
                sa.Column("repo_origin", sa.String(200), nullable=True),
            )
        if "metadata" not in existing_node_cols:
            op.add_column(
                "dependency_nodes",
                sa.Column("metadata", sa.JSON(), nullable=True),
            )

        # Indexes — guard with IF NOT EXISTS (PostgreSQL supports this natively)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_dependency_nodes_canonical_id
            ON dependency_nodes (canonical_id)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_dependency_nodes_repo_origin
            ON dependency_nodes (repo_origin)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS ix_dependency_nodes_mig_canonical
            ON dependency_nodes (migration_id, canonical_id)
        """)

    # ── 2. Add new columns to dependency_edges (idempotent) ─────────────
    if inspector.has_table("dependency_edges"):
        existing_edge_cols = {c["name"] for c in inspector.get_columns("dependency_edges")}

        if "edge_type" not in existing_edge_cols:
            op.add_column(
                "dependency_edges",
                sa.Column(
                    "edge_type",
                    sa.String(30),
                    nullable=False,
                    server_default="depends_on",
                ),
            )
        if "evidence" not in existing_edge_cols:
            op.add_column(
                "dependency_edges",
                sa.Column("evidence", sa.JSON(), nullable=True),
            )

    # ── 3. Data migration: fold service_inventories into metadata ───────
    # Guard: only run if both source and target tables exist and we're on PostgreSQL.
    if (
        bind.dialect.name == "postgresql"
        and inspector.has_table("service_inventories")
        and inspector.has_table("dependency_nodes")
    ):
        op.execute(
            """
            UPDATE dependency_nodes n
            SET metadata = COALESCE(n.metadata, '{}'::json)::jsonb || jsonb_build_object(
                'detection_frequency', i.detection_frequency,
                'detections_by',       i.detections_by
            )
            FROM service_inventories i
            WHERE i.migration_id = n.migration_id
              AND LOWER(i.cloud_provider) = LOWER(n.cloud_provider)
              AND LOWER(i.service_name)   = LOWER(n.service_name);
            """
        )

    # NOTE: service_inventories table is intentionally NOT dropped here.
    # A follow-up migration will drop it once the code is confirmed not
    # to write to it anymore.


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop indexes with IF EXISTS (PostgreSQL native — safe even if already gone)
    op.execute("DROP INDEX IF EXISTS ix_dependency_nodes_mig_canonical")
    op.execute("DROP INDEX IF EXISTS ix_dependency_nodes_repo_origin")
    op.execute("DROP INDEX IF EXISTS ix_dependency_nodes_canonical_id")

    if inspector.has_table("dependency_nodes"):
        existing_node_cols = {c["name"] for c in inspector.get_columns("dependency_nodes")}
        if "metadata" in existing_node_cols:
            op.drop_column("dependency_nodes", "metadata")
        if "repo_origin" in existing_node_cols:
            op.drop_column("dependency_nodes", "repo_origin")
        if "canonical_id" in existing_node_cols:
            op.drop_column("dependency_nodes", "canonical_id")

    if inspector.has_table("dependency_edges"):
        existing_edge_cols = {c["name"] for c in inspector.get_columns("dependency_edges")}
        if "evidence" in existing_edge_cols:
            op.drop_column("dependency_edges", "evidence")
        if "edge_type" in existing_edge_cols:
            op.drop_column("dependency_edges", "edge_type")
