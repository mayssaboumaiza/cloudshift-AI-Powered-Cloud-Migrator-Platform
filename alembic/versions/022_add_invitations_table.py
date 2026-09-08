"""022 — Add invitations table

Revision ID: 022_add_invitations_table
Revises: 021_remove_viewer_role
Create Date: 2026-06-05
"""
from alembic import op
import sqlalchemy as sa

revision = "022_add_invitations_table"
down_revision = "021_remove_viewer_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            id          VARCHAR(36)  PRIMARY KEY,
            token       VARCHAR(64)  NOT NULL UNIQUE,
            email       VARCHAR(255) NOT NULL,
            role        userrole     NOT NULL,
            invited_by  VARCHAR(36)  REFERENCES users(id) ON DELETE SET NULL,
            expires_at  TIMESTAMPTZ  NOT NULL,
            used_at     TIMESTAMPTZ,
            created_at  TIMESTAMPTZ  NOT NULL
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_invitations_token ON invitations (token)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_invitations_email ON invitations (email)")


def downgrade() -> None:
    op.drop_index("ix_invitations_email",  table_name="invitations")
    op.drop_index("ix_invitations_token",  table_name="invitations")
    op.drop_table("invitations")
