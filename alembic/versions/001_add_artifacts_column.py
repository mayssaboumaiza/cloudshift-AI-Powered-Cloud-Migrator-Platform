"""add artifacts column to migrations

Revision ID: 001_add_artifacts_column
Revises:
Create Date: 2026-04-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_add_artifacts_column"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Guard: if the table doesn't exist yet (fresh DB in ALEMBIC_MANAGED mode),
    # skip — create_all (dev) or a future 000_initial migration handles schema creation.
    if not inspector.has_table("migrations"):
        return

    # Idempotency: skip if the column was already added (e.g. by a previous create_all run).
    existing = {col["name"] for col in inspector.get_columns("migrations")}
    if "artifacts" not in existing:
        op.add_column("migrations", sa.Column("artifacts", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("migrations"):
        existing = {col["name"] for col in inspector.get_columns("migrations")}
        if "artifacts" in existing:
            op.drop_column("migrations", "artifacts")
