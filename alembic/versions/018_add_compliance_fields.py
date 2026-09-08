"""018 — Add compliance, HA, and network isolation fields

Revision ID: 018_add_compliance_fields
Revises: 017_add_incremental_migration
Create Date: 2026-05-31

Adds three structured constraint columns to replace the free-text
regulatory_constraints field with actionable, pipeline-integrated data:

  compliance_standards      : JSON list  — ['GDPR', 'HIPAA', 'PCI-DSS', ...]
  high_availability_required: Boolean    — controls HA block generation
  network_isolation_required : Boolean   — controls public_network_access

The old regulatory_constraints column is kept for backward compatibility
but is no longer the primary source of compliance configuration.
"""
from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _column_exists

revision = "018_add_compliance_fields"
down_revision = "017_add_incremental_migration"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("compliance_standards",       sa.JSON(),    None,    "List of compliance standards: ['GDPR','HIPAA','PCI-DSS','ISO27001','SOC2']"),
    ("high_availability_required", sa.Boolean(), "false", "When true, Agent 02 generates HA blocks and validates AZ support in the region"),
    ("network_isolation_required", sa.Boolean(), "false", "When true, public_network_access_enabled=false on all resources"),
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
