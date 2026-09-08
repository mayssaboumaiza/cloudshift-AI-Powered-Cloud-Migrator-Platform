"""
data/models/secret_model.py — DB models for Phase 1 secret lifecycle.

MigrationSecret  : maps a secret_ref (UUID) → vault_path + migration context.
                   The DB NEVER stores plaintext values — only the vault path.

SecretAuditLog   : append-only record of every store/retrieve/delete action.
                   Written on every SecretBroker interaction for compliance.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from configuration.database import Base


class MigrationSecret(Base):
    """One row per (migration, role) pair — references a vault path, never a value."""

    __tablename__ = "migration_secrets"
    __table_args__ = (
        UniqueConstraint("migration_id", "role", name="uq_migration_secrets_migration_role"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Public secret_ref returned to callers",
    )
    migration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("migrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # github_token | aws | gcp | azure | ssh_key
    role: Mapped[str] = mapped_column(String(50), nullable=False)

    # Which backend holds the secret ("vault" | "file")
    backend: Mapped[str] = mapped_column(String(20), nullable=False, default="vault")

    # Path inside the backend (e.g. "migrations/{id}/github_token")
    vault_path: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at


class SecretAuditLog(Base):
    """Append-only audit trail for every secret operation."""

    __tablename__ = "secret_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Nullable: secret may be deleted by the time we audit
    secret_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("migration_secrets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Denormalised — kept after secret deletion for forensics
    migration_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # store | retrieve | delete | rotate
    action: Mapped[str] = mapped_column(String(20), nullable=False)

    # system | user | worker
    actor: Mapped[str] = mapped_column(String(50), nullable=False, default="system")

    # Client IP — nullable (internal workers have no public IP)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)

    # Any extra structured context (request_id, agent name, etc.)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
