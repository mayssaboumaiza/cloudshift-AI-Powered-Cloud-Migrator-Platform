"""Add cloud_service_ontology and migration_audit tables.

cloud_service_ontology — curated AWS/Azure/GCP equivalence graph used by
  the hybrid scoring system (embedding 0.5 + ontology 0.3 + schema 0.2).

migration_audit — append-only log of every AWS→Azure mapping decision
  produced by Agent 01. Used for post-hoc error detection and feedback loops.

Revision ID: 014_add_ontology_and_audit
Revises:     013_add_region_availability_cache
Create Date: 2026-05-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from db_utils.schema_guards import _table_exists, _index_exists

revision: str = "014_add_ontology_and_audit"
down_revision: Union[str, None] = "013_add_region_availability_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ONTOLOGY_INDEXES = [
    ("ix_ontology_source", ["source_provider", "source_service"],                                         False),
    ("ix_ontology_pair",   ["source_provider", "source_service", "target_provider", "target_service"],    True),
]
_AUDIT_INDEXES = [
    ("ix_audit_migration_id", ["migration_id"]),
    ("ix_audit_source_svc",   ["source_service"]),
    ("ix_audit_target_svc",   ["target_service"]),
]


def upgrade() -> None:
    conn = op.get_bind()

    if not _table_exists(conn, "cloud_service_ontology"):
        op.create_table(
            "cloud_service_ontology",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("source_provider",     sa.String(16),  nullable=False),
            sa.Column("source_service",      sa.String(120), nullable=False),
            sa.Column("target_provider",     sa.String(16),  nullable=False),
            sa.Column("target_service",      sa.String(120), nullable=False),
            sa.Column("equivalence_score",   sa.Float(),     nullable=False, default=0.0),
            sa.Column("compatibility_score", sa.Float(),     nullable=False, default=0.0),
            sa.Column("confidence",          sa.Float(),     nullable=False, default=1.0),
            sa.Column("migration_type",      sa.String(16),  nullable=False, default="REPLATFORM"),
            sa.Column("notes",               sa.Text(),      nullable=True),
            sa.Column("last_updated",        sa.DateTime(timezone=True),
                      server_default=sa.func.now(), onupdate=sa.func.now()),
        )
    for idx_name, cols, unique in _ONTOLOGY_INDEXES:
        if not _index_exists(conn, idx_name):
            op.create_index(idx_name, "cloud_service_ontology", cols, unique=unique)

    if not _table_exists(conn, "migration_audit"):
        op.create_table(
            "migration_audit",
            sa.Column("id",               sa.Integer(),    primary_key=True, autoincrement=True),
            sa.Column("migration_id",     sa.String(64),   nullable=False),
            sa.Column("service_name",     sa.String(120),  nullable=False),
            sa.Column("source_provider",  sa.String(16),   nullable=False),
            sa.Column("source_service",   sa.String(120),  nullable=False),
            sa.Column("target_provider",  sa.String(16),   nullable=False),
            sa.Column("target_service",   sa.String(120),  nullable=False),
            sa.Column("strategy_7r",      sa.String(16),   nullable=False),
            sa.Column("equivalence_score",sa.Float(),      nullable=True),
            sa.Column("saw_score",        sa.Float(),      nullable=True),
            sa.Column("ontology_score",   sa.Float(),      nullable=True),
            sa.Column("confidence",       sa.Float(),      nullable=True),
            sa.Column("intent_valid",     sa.Boolean(),    nullable=True),
            sa.Column("intent_mismatch",  sa.Text(),       nullable=True),
            sa.Column("reasoning",        sa.Text(),       nullable=True),
            sa.Column("created_at",       sa.DateTime(timezone=True),
                      server_default=sa.func.now()),
        )
    for idx_name, cols in _AUDIT_INDEXES:
        if not _index_exists(conn, idx_name):
            op.create_index(idx_name, "migration_audit", cols)


def downgrade() -> None:
    conn = op.get_bind()
    for idx_name, _ in reversed(_AUDIT_INDEXES):
        if _index_exists(conn, idx_name):
            op.drop_index(idx_name, table_name="migration_audit")
    if _table_exists(conn, "migration_audit"):
        op.drop_table("migration_audit")
    for idx_name, _, _ in reversed(_ONTOLOGY_INDEXES):
        if _index_exists(conn, idx_name):
            op.drop_index(idx_name, table_name="cloud_service_ontology")
    if _table_exists(conn, "cloud_service_ontology"):
        op.drop_table("cloud_service_ontology")
