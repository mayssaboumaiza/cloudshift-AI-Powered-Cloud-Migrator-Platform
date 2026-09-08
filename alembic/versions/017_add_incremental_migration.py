"""017 — Add incremental migration support (Path 2)

Revision ID: 017_add_incremental_migration
Revises: 016_add_users_table
Create Date: 2026-05-31

Adds three columns to the migrations table to support Path 2 (incremental):
  - last_git_sha      : SHA of the last commit analyzed in a successful migration
  - migration_mode    : 'full' (Path 1) or 'incremental' (Path 2)
  - detected_changes  : JSON summary of what changed (new resources, modified files)
"""
from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _column_exists

revision = "017_add_incremental_migration"
down_revision = "016_add_users_table"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("last_git_sha",    sa.String(40),  None,   "Git commit SHA of the last successfully analyzed repo state"),
    ("migration_mode",  sa.String(20),  "full", "full = Path 1 (from scratch) | incremental = Path 2 (delta only)"),
    ("detected_changes", sa.JSON(),     None,   "JSON: {new_resources, modified_files, removed_files, current_sha, previous_sha}"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for col_name, col_type, default, comment in _COLUMNS:
        if not _column_exists(conn, "migrations", col_name):
            op.add_column("migrations", sa.Column(
                col_name, col_type,
                nullable=True,
                server_default=default,
                comment=comment,
            ))


def downgrade() -> None:
    conn = op.get_bind()
    for col_name, *_ in reversed(_COLUMNS):
        if _column_exists(conn, "migrations", col_name):
            op.drop_column("migrations", col_name)
