"""v008: pipeline_events table — dedicated SSE event rows (replaces events JSON array).

Changes:
1. CREATE pipeline_events (id serial PK, thread_id, event_type, phase, progress, payload, created_at)

pipeline_state.events is kept (not dropped) for backward-compat reads.
New SSE events are written exclusively to pipeline_events (one INSERT per event,
integer PK used as a stable SSE cursor — no JSON array overwrite race condition).

Revision ID: 008_pipeline_events_and_fixes
Revises: 007_consolidate_tables
Create Date: 2026-05-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from db_utils.schema_guards import _index_exists

revision: str = "008_pipeline_events_and_fixes"
down_revision: Union[str, Sequence[str], None] = "007_consolidate_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # 1. Create pipeline_events table
    if "pipeline_events" not in existing_tables:
        op.create_table(
            "pipeline_events",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("thread_id", sa.String(36), nullable=False),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("phase", sa.String(50), nullable=False),
            sa.Column("progress", sa.Integer(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        conn = op.get_bind()
        if not _index_exists(conn, "ix_pipeline_events_thread_id"):
            op.create_index("ix_pipeline_events_thread_id", "pipeline_events", ["thread_id"])
        if not _index_exists(conn, "ix_pipeline_events_created_at"):
            op.create_index("ix_pipeline_events_created_at", "pipeline_events", ["created_at"])



def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    if "pipeline_events" in existing_tables:
        op.drop_index("ix_pipeline_events_created_at", table_name="pipeline_events")
        op.drop_index("ix_pipeline_events_thread_id", table_name="pipeline_events")
        op.drop_table("pipeline_events")

