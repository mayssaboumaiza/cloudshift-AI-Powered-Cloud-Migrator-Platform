"""021 — Remove viewer role from userrole enum

Revision ID: 021_remove_viewer_role
Revises: 020_add_audit_logs
Create Date: 2026-06-05
"""
from alembic import op

revision = "021_remove_viewer_role"
down_revision = "020_add_audit_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Reassign any existing viewer users to analyst
    op.execute("UPDATE users SET role = 'analyst' WHERE role::text = 'viewer'")

    # Step 1 — drop default and convert column to plain text
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE text "
        "USING role::text"
    )

    # Step 2 — recreate enum without 'viewer' (idempotent: drop if exists)
    op.execute("DROP TYPE IF EXISTS userrole")
    op.execute("CREATE TYPE userrole AS ENUM ('admin', 'analyst')")

    # Step 3 — convert column back to new enum and restore default
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE userrole "
        "USING role::text::userrole"
    )
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'analyst'")


def downgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN role DROP DEFAULT")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE text USING role::text")
    op.execute("DROP TYPE userrole")
    op.execute("CREATE TYPE userrole AS ENUM ('admin', 'analyst', 'viewer')")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING role::userrole")
    op.execute("ALTER TABLE users ALTER COLUMN role SET DEFAULT 'analyst'")
