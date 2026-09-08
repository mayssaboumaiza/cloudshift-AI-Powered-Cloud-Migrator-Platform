"""
audit_log_model.py — Immutable audit trail for GDPR compliance.

Every significant action (migration created/started/accepted/rejected,
user created/modified/deleted, credentials stored) is recorded here.
Rows are append-only — never updated or deleted.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from configuration.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    # Who did the action
    user_id:    Mapped[str | None] = mapped_column(String(36),  nullable=True, index=True)
    username:   Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_role:  Mapped[str | None] = mapped_column(String(20),  nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45),  nullable=True)

    # What action
    action:      Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # e.g. "migration.created", "migration.analyze.started", "user.deleted", "auth.login"

    # On what resource
    resource_type: Mapped[str | None] = mapped_column(String(50),  nullable=True)
    resource_id:   Mapped[str | None] = mapped_column(String(36),  nullable=True, index=True)
    resource_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Extra context (JSON)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # HTTP context
    method:    Mapped[str | None] = mapped_column(String(10),  nullable=True)
    path:      Mapped[str | None] = mapped_column(String(500), nullable=True)
    status_code: Mapped[int | None] = mapped_column(nullable=True)

    # Outcome
    success: Mapped[bool] = mapped_column(default=True, nullable=False)
    error:   Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamp (UTC, immutable)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
