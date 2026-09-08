"""020 — Add audit_logs table for GDPR compliance audit trail

Revision ID: 020_add_audit_logs
Revises: 019_add_github_output_urls
Create Date: 2026-06-05

Append-only table. Rows are never updated or deleted.
Indexes on: user_id, action, resource_id, created_at.
"""
from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _table_exists, _index_exists

revision = "020_add_audit_logs"
down_revision = "019_add_github_output_urls"
branch_labels = None
depends_on = None

_INDEXES = [
    ("idx_audit_user_id",     ["user_id"]),
    ("idx_audit_action",      ["action"]),
    ("idx_audit_resource_id", ["resource_id"]),
    ("idx_audit_created_at",  ["created_at"]),
]


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id",            sa.String(36),  primary_key=True),
            sa.Column("user_id",       sa.String(36),  nullable=True),
            sa.Column("username",      sa.String(100), nullable=True),
            sa.Column("user_role",     sa.String(20),  nullable=True),
            sa.Column("ip_address",    sa.String(45),  nullable=True),
            sa.Column("action",        sa.String(100), nullable=False),
            sa.Column("resource_type", sa.String(50),  nullable=True),
            sa.Column("resource_id",   sa.String(36),  nullable=True),
            sa.Column("resource_name", sa.String(200), nullable=True),
            sa.Column("details",       sa.JSON,        nullable=True),
            sa.Column("method",        sa.String(10),  nullable=True),
            sa.Column("path",          sa.String(500), nullable=True),
            sa.Column("status_code",   sa.Integer,     nullable=True),
            sa.Column("success",       sa.Boolean,     nullable=False, default=True),
            sa.Column("error",         sa.Text,        nullable=True),
            sa.Column("created_at",    sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.func.now()),
        )
    for idx_name, cols in _INDEXES:
        if not _index_exists(conn, idx_name):
            op.create_index(idx_name, "audit_logs", cols)


def downgrade() -> None:
    conn = op.get_bind()
    for idx_name, _ in reversed(_INDEXES):
        if _index_exists(conn, idx_name):
            op.drop_index(idx_name, table_name="audit_logs")
    if _table_exists(conn, "audit_logs"):
        op.drop_table("audit_logs")
