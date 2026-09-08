"""
data/repositories/secret_repository.py — Data access layer for secret lifecycle.

All operations are async (SQLAlchemy async session).
Values are NEVER passed through or stored here — only metadata and vault paths.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.secret_model import MigrationSecret, SecretAuditLog
from data.custom_data_exceptions import GeneralDatabaseError

logger = logging.getLogger("SecretRepository")


class SecretRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── MigrationSecret CRUD ──────────────────────────────────────────────────

    async def create(
        self,
        migration_id: str,
        role: str,
        backend: str,
        vault_path: str,
        expires_at: datetime | None = None,
    ) -> MigrationSecret:
        """Insert a new secret reference row. Raises if (migration_id, role) already exists."""
        record = MigrationSecret(
            id=str(uuid.uuid4()),
            migration_id=migration_id,
            role=role,
            backend=backend,
            vault_path=vault_path,
            expires_at=expires_at,
        )
        self.db.add(record)
        try:
            await self.db.commit()
            await self.db.refresh(record)
            return record
        except IntegrityError as exc:
            await self.db.rollback()
            raise GeneralDatabaseError(
                f"Secret ref already exists for migration={migration_id} role={role}: {exc}"
            ) from exc
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise GeneralDatabaseError(str(exc)) from exc

    async def upsert(
        self,
        migration_id: str,
        role: str,
        backend: str,
        vault_path: str,
        expires_at: datetime | None = None,
    ) -> MigrationSecret:
        """Create or replace secret ref for (migration_id, role)."""
        existing = await self.get_by_role(migration_id, role)
        if existing:
            existing.backend = backend
            existing.vault_path = vault_path
            existing.expires_at = expires_at
            existing.last_accessed_at = None
            try:
                await self.db.commit()
                await self.db.refresh(existing)
                return existing
            except SQLAlchemyError as exc:
                await self.db.rollback()
                raise GeneralDatabaseError(str(exc)) from exc
        return await self.create(migration_id, role, backend, vault_path, expires_at)

    async def get(self, secret_ref: str) -> MigrationSecret | None:
        """Fetch secret metadata by secret_ref (UUID)."""
        try:
            result = await self.db.execute(
                select(MigrationSecret).where(MigrationSecret.id == secret_ref)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise GeneralDatabaseError(str(exc)) from exc

    async def get_by_role(self, migration_id: str, role: str) -> MigrationSecret | None:
        """Fetch secret metadata for a specific migration + role."""
        try:
            result = await self.db.execute(
                select(MigrationSecret).where(
                    MigrationSecret.migration_id == migration_id,
                    MigrationSecret.role == role,
                )
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise GeneralDatabaseError(str(exc)) from exc

    async def list_for_migration(self, migration_id: str) -> list[MigrationSecret]:
        """List all secret refs for a migration (metadata only, no values)."""
        try:
            result = await self.db.execute(
                select(MigrationSecret)
                .where(MigrationSecret.migration_id == migration_id)
                .order_by(MigrationSecret.created_at)
            )
            return list(result.scalars().all())
        except SQLAlchemyError as exc:
            raise GeneralDatabaseError(str(exc)) from exc

    async def delete(self, secret_ref: str) -> bool:
        """Delete secret ref row. Returns True if deleted, False if not found."""
        record = await self.get(secret_ref)
        if not record:
            return False
        try:
            await self.db.delete(record)
            await self.db.commit()
            return True
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise GeneralDatabaseError(str(exc)) from exc

    async def touch(self, secret_ref: str) -> None:
        """Update last_accessed_at timestamp after a JIT retrieval."""
        try:
            await self.db.execute(
                update(MigrationSecret)
                .where(MigrationSecret.id == secret_ref)
                .values(last_accessed_at=datetime.now(timezone.utc))
            )
            await self.db.commit()
        except SQLAlchemyError as exc:
            await self.db.rollback()
            raise GeneralDatabaseError(str(exc)) from exc

    # ── SecretAuditLog ────────────────────────────────────────────────────────

    async def audit(
        self,
        action: str,
        secret_id: str | None = None,
        migration_id: str | None = None,
        role: str | None = None,
        actor: str = "system",
        ip_address: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write one append-only audit record. Never raises — audit failures must not block the caller."""
        try:
            entry = SecretAuditLog(
                secret_id=secret_id,
                migration_id=migration_id,
                role=role,
                action=action,
                actor=actor,
                ip_address=ip_address,
                extra=extra or {},
            )
            self.db.add(entry)
            await self.db.commit()
        except Exception as exc:  # noqa: BLE001
            await self.db.rollback()
            logger.error(
                "Failed to write audit log: action=%s migration=%s role=%s error=%s",
                action, migration_id, role, exc,
            )
