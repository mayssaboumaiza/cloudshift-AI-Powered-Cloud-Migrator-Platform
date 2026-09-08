"""Drop service_inventories table (data folded into dependency_nodes.metadata in 002)

The service_inventories table was intentionally kept alive in migration 002 to give
callers a deprecation cycle. All data has been merged into dependency_nodes.metadata
(detection_frequency, detections_by). No code writes to service_inventories anymore.

Revision ID: 003_drop_service_inventories
Revises: 002_consolidate_dependency_schema
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003_drop_service_inventories"
down_revision: Union[str, Sequence[str], None] = "002_consolidate_dependency_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("service_inventories"):
        return  # already dropped or never created

    op.drop_table("service_inventories")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("service_inventories"):
        return  # already exists

    op.create_table(
        "service_inventories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("migration_id", sa.String(36), nullable=False, index=True),
        sa.Column("cloud_provider", sa.String(50), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("detection_frequency", sa.Integer(), nullable=True),
        sa.Column("detections_by", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
