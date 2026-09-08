"""Add warnings column to migrations table.

Required by alerts_router.py which aggregates pipeline warnings
(non-blocking issues like partial IaC coverage, drift, intent mismatches)
separately from hard errors.

Revision ID: 015_add_warnings_column
Revises:     014_add_ontology_and_audit
Create Date: 2026-05-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _column_exists

revision: str = "015_add_warnings_column"
down_revision: Union[str, None] = "014_add_ontology_and_audit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if not _column_exists(conn, "migrations", "warnings"):
        op.add_column(
            "migrations",
            sa.Column(
                "warnings",
                sa.JSON(),
                nullable=True,
                server_default="[]",
                comment="Non-blocking pipeline warnings (checkov low, drift, partial coverage)",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _column_exists(conn, "migrations", "warnings"):
        op.drop_column("migrations", "warnings")
