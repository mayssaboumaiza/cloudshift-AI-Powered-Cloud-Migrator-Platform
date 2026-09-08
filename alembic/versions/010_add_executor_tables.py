"""v010: Phase 2 executor — runner_jobs + deployment_logs tables.

runner_jobs      : PostgreSQL job queue (SKIP LOCKED pattern).
                   One row per deployment attempt; claimed by a worker process.
deployment_logs  : Append-only log stream from the terraform subprocess.

Revision ID: 010_add_executor_tables
Revises: 009_add_secrets_tables
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010_add_executor_tables"
down_revision: Union[str, Sequence[str], None] = "009_add_secrets_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── runner_jobs ─────────────────────────────────────────────────────────────
    if "runner_jobs" not in existing_tables:
        op.create_table(
            "runner_jobs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "migration_id",
                sa.String(36),
                sa.ForeignKey("migrations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("worker_id", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("plan_summary", sa.JSON(), nullable=True),
            sa.Column("apply_outputs", sa.JSON(), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_runner_jobs_migration_id ON runner_jobs (migration_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_runner_jobs_status       ON runner_jobs (status)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_runner_jobs_created_at   ON runner_jobs (created_at)")
        op.execute("""
            ALTER TABLE runner_jobs
            ADD CONSTRAINT uq_runner_jobs_migration_id UNIQUE (migration_id)
        """)

    # ── deployment_logs ──────────────────────────────────────────────────────────
    if "deployment_logs" not in existing_tables:
        op.create_table(
            "deployment_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "job_id",
                sa.String(36),
                sa.ForeignKey("runner_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("stage", sa.String(30), nullable=False),
            sa.Column("stream", sa.String(10), nullable=False, server_default="stdout"),
            sa.Column("line", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_deployment_logs_job_id     ON deployment_logs (job_id)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_deployment_logs_created_at ON deployment_logs (created_at)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = inspector.get_table_names()
    if "deployment_logs" in existing_tables:
        op.drop_table("deployment_logs")
    if "runner_jobs" in existing_tables:
        op.drop_table("runner_jobs")
