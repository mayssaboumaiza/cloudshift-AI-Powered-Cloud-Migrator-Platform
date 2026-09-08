"""Add region_availability_cache table.

Stores live + static region availability results so that
services/region_checker.py doesn't re-hit cloud pricing APIs
on every scoring call.

TTLs:
  live results  : 24 hours  (updated by CACHE_TTL_LIVE)
  static results: 7 days    (updated by CACHE_TTL_STATIC)

Revision ID: 013_add_region_availability_cache
Revises:     012_add_reconciliation_indexes
Create Date: 2026-05-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from db_utils.schema_guards import _table_exists, _index_exists

revision: str = "013_add_region_availability_cache"
down_revision: Union[str, None] = "012_add_reconciliation_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES = [
    ("uq_region_availability_cache_key", ["service_name", "region", "provider"], True),
    ("ix_region_cache_provider_region",  ["provider", "region"],                 False),
    ("ix_region_cache_expires_at",       ["expires_at"],                         False),
]


def upgrade() -> None:
    conn = op.get_bind()
    if not _table_exists(conn, "region_availability_cache"):
        op.create_table(
            "region_availability_cache",
            sa.Column("id",                sa.Integer,     primary_key=True, autoincrement=True),
            sa.Column("service_name",      sa.String(256), nullable=False),
            sa.Column("region",            sa.String(128), nullable=False),
            sa.Column("provider",          sa.String(32),  nullable=False),
            sa.Column("available",         sa.Boolean,     nullable=False, default=False),
            sa.Column("availability_type", sa.String(32),  nullable=True),
            sa.Column("source",            sa.String(128), nullable=True),
            sa.Column("checked_at",        sa.DateTime,    nullable=True),
            sa.Column("expires_at",        sa.DateTime,    nullable=True),
        )
    for idx_name, cols, unique in _INDEXES:
        if not _index_exists(conn, idx_name):
            op.create_index(idx_name, "region_availability_cache", cols, unique=unique)


def downgrade() -> None:
    conn = op.get_bind()
    for idx_name, _, _ in reversed(_INDEXES):
        if _index_exists(conn, idx_name):
            op.drop_index(idx_name, table_name="region_availability_cache")
    if _table_exists(conn, "region_availability_cache"):
        op.drop_table("region_availability_cache")
