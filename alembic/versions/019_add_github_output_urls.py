"""019 — Add github_pr_url and github_repo_url columns to migrations

Revision ID: 019_add_github_output_urls
Revises: 018_add_compliance_fields
Create Date: 2026-06-04

Stores the GitHub output repo URL and PR URL directly on the migrations row
so the API response can expose them as top-level fields without requiring
a full artifacts JSON parse on the client side.

Both columns are nullable — they are populated by publish_github_node
only when the pipeline reaches the publish phase successfully.
"""
from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _column_exists

revision = "019_add_github_output_urls"
down_revision = "018_add_compliance_fields"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("github_pr_url",   sa.String(500)),
    ("github_repo_url", sa.String(500)),
]


def upgrade() -> None:
    conn = op.get_bind()
    for col_name, col_type in _COLUMNS:
        if not _column_exists(conn, "migrations", col_name):
            op.add_column("migrations", sa.Column(col_name, col_type, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    for col_name, _ in reversed(_COLUMNS):
        if _column_exists(conn, "migrations", col_name):
            op.drop_column("migrations", col_name)
