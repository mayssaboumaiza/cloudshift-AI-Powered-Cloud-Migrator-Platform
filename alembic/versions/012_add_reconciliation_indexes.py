"""Add indexes for state-reconciliation hot-path queries.

Creates:
  ix_runner_jobs_status          — speeds up resolve_canonical_state status lookups
  ix_pipeline_state_thread_id    — speeds up checkpoint->>'thread_id' filter in
                                   resolve_canonical_state fallback query

No schema changes — indexes only.

Revision ID: 012_add_reconciliation_indexes
Revises:     011_runner_failure_classification
Create Date: 2026-05-14
"""
from typing import Sequence, Union

from alembic import op

revision: str = "012_add_reconciliation_indexes"
down_revision: Union[str, None] = "011_runner_failure_classification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Index on runner_jobs.status — used by set_status, claim_next, resolve_canonical_state
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_runner_jobs_status "
        "ON runner_jobs (status)"
    )
    # Functional index on pipeline_state checkpoint JSON thread_id field
    # Allows the reconciliation fallback query to avoid a full-table scan
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_pipeline_state_thread_id "
        "ON pipeline_state ((checkpoint->>'thread_id'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_pipeline_state_thread_id")
    op.execute("DROP INDEX IF EXISTS ix_runner_jobs_status")
