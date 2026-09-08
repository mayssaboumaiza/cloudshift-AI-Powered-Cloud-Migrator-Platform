"""016 — Add users table with roles

Revision ID: 016_add_users_table
Revises: 015_add_warnings_column
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _table_exists, _index_exists, _type_exists

revision = "016_add_users_table"
down_revision = "015_add_warnings_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if not _type_exists(conn, "userrole"):
        op.execute("CREATE TYPE userrole AS ENUM ('admin', 'analyst')")
    if not _table_exists(conn, "users"):
        op.create_table(
            "users",
            sa.Column("id",              sa.String(36),  primary_key=True, nullable=False),
            sa.Column("email",           sa.String(255), nullable=False),
            sa.Column("username",        sa.String(80),  nullable=False),
            sa.Column("hashed_password", sa.String(255), nullable=False),
            sa.Column(
                "role",
                sa.Enum("admin", "analyst", name="userrole", create_type=False),
                nullable=False,
                server_default="analyst",
            ),
            sa.Column("is_active",  sa.Boolean(),               nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    if not _index_exists(conn, "ix_users_email"):
        op.create_index("ix_users_email",    "users", ["email"],    unique=True)
    if not _index_exists(conn, "ix_users_username"):
        op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    if _index_exists(conn, "ix_users_username"):
        op.drop_index("ix_users_username", table_name="users")
    if _index_exists(conn, "ix_users_email"):
        op.drop_index("ix_users_email", table_name="users")
    if _table_exists(conn, "users"):
        op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS userrole")
