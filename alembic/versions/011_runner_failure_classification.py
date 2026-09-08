"""v011: Structured failure classification for runner_jobs.

Adds error_class / error_detail columns so the wait_runner node can
read a classified failure and route accordingly.

Replaces the hard UNIQUE constraint on migration_id with a partial
unique index that only blocks new enqueues while a job is still
in-flight (status NOT IN ('done','failed')), enabling the retry path.

Revision ID: 011_runner_failure_classification
Revises:     010_add_executor_tables
Create Date: 2026-05-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011_runner_failure_classification"
down_revision: Union[str, Sequence[str], None] = "010_add_executor_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("runner_jobs")}
    indexes = {i["name"] for i in inspector.get_indexes("runner_jobs")}

    if "error_class" not in columns:
        op.add_column(
            "runner_jobs",
            sa.Column("error_class", sa.String(30), nullable=True),
        )

    if "error_detail" not in columns:
        op.add_column(
            "runner_jobs",
            sa.Column("error_detail", sa.JSON(), nullable=True),
        )

    # Drop the hard unique constraint (blocks retry enqueues)
    try:
        op.drop_constraint(
            "uq_runner_jobs_migration_id", "runner_jobs", type_="unique"
        )
    except Exception:
        pass  # already absent

    # Partial unique index: at most one non-terminal job per migration
    if "uq_runner_jobs_migration_active" not in indexes:
        op.execute(
            "CREATE UNIQUE INDEX uq_runner_jobs_migration_active "
            "ON runner_jobs (migration_id) "
            "WHERE status NOT IN ('done', 'failed')"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {i["name"] for i in inspector.get_indexes("runner_jobs")}

    if "uq_runner_jobs_migration_active" in indexes:
        op.drop_index(
            "uq_runner_jobs_migration_active", table_name="runner_jobs"
        )

    op.drop_column("runner_jobs", "error_detail")
    op.drop_column("runner_jobs", "error_class")

    # Restore hard constraint — operator must ensure no duplicate rows first:
    #   DELETE FROM runner_jobs rj USING runner_jobs rj2
    #   WHERE rj.migration_id = rj2.migration_id AND rj.created_at < rj2.created_at;
    op.execute(
        "ALTER TABLE runner_jobs "
        "ADD CONSTRAINT uq_runner_jobs_migration_id UNIQUE (migration_id)"
    )
