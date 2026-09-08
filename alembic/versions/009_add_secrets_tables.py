"""v009: Phase 1 secret lifecycle — migration_secrets + secret_audit_log tables.

migration_secrets : tracks vault path references per migration+role.
                    The DB stores NO plaintext values — only the path inside the
                    configured SecretBroker backend (Vault KV v2 or encrypted file).

secret_audit_log  : append-only audit trail (store/retrieve/delete actions).

Revision ID: 009_add_secrets_tables
Revises: 008_pipeline_events_and_fixes
Create Date: 2026-05-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009_add_secrets_tables"
down_revision: Union[str, Sequence[str], None] = "008_pipeline_events_and_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    # ── migration_secrets ─────────────────────────────────────────────────────
    if "migration_secrets" not in existing:
        op.create_table(
            "migration_secrets",
            sa.Column("id", sa.String(36), primary_key=True, nullable=False),
            sa.Column(
                "migration_id",
                sa.String(36),
                sa.ForeignKey("migrations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(50), nullable=False),
            sa.Column("backend", sa.String(20), nullable=False, server_default="vault"),
            sa.Column("vault_path", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_migration_secrets_migration_id",
            "migration_secrets",
            ["migration_id"],
        )
        # Enforce: one secret_ref per (migration, role) — callers must upsert explicitly
        op.create_unique_constraint(
            "uq_migration_secrets_migration_role",
            "migration_secrets",
            ["migration_id", "role"],
        )

    # ── secret_audit_log ──────────────────────────────────────────────────────
    if "secret_audit_log" not in existing:
        op.create_table(
            "secret_audit_log",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True, nullable=False),
            sa.Column(
                "secret_id",
                sa.String(36),
                sa.ForeignKey("migration_secrets.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("migration_id", sa.String(36), nullable=True),
            sa.Column("role", sa.String(50), nullable=True),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("actor", sa.String(50), nullable=False, server_default="system"),
            sa.Column("ip_address", sa.String(45), nullable=True),
            sa.Column("extra", sa.JSON(), nullable=True, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_secret_audit_log_secret_id",
            "secret_audit_log",
            ["secret_id"],
        )
        op.create_index(
            "ix_secret_audit_log_migration_id",
            "secret_audit_log",
            ["migration_id"],
        )
        op.create_index(
            "ix_secret_audit_log_created_at",
            "secret_audit_log",
            ["created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = inspector.get_table_names()

    if "secret_audit_log" in existing:
        op.drop_index("ix_secret_audit_log_created_at", table_name="secret_audit_log")
        op.drop_index("ix_secret_audit_log_migration_id", table_name="secret_audit_log")
        op.drop_index("ix_secret_audit_log_secret_id", table_name="secret_audit_log")
        op.drop_table("secret_audit_log")

    if "migration_secrets" in existing:
        op.drop_constraint(
            "uq_migration_secrets_migration_role", "migration_secrets", type_="unique"
        )
        op.drop_index("ix_migration_secrets_migration_id", table_name="migration_secrets")
        op.drop_table("migration_secrets")
