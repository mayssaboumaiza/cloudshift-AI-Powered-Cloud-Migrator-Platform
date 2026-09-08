"""
migration_repository.py - Data Access Layer for Migration entity.
"""
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.migration_model import Migration
from data.custom_data_exceptions import (
    MigrationNotFoundDB,
    MigrationAlreadyExistsDB,
    GeneralDatabaseError,
)


class MigrationRepository:
    """Data Access Layer for Migration."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, migration_id: str) -> Migration:
        try:
            result = await self.db.execute(
                select(Migration).where(Migration.id == migration_id)
            )
            migration = result.scalar_one_or_none()
            if not migration:
                raise MigrationNotFoundDB(f"Migration with id {migration_id} not found")
            return migration
        except MigrationNotFoundDB:
            raise
        except SQLAlchemyError as e:
            raise GeneralDatabaseError(str(e)) from e

    async def get_by_id_for_user(
        self, migration_id: str, user_id: str, is_admin: bool = False
    ) -> Migration:
        """Ownership-aware get. Admins see all; analysts only see their own rows.

        Rows with user_id=NULL (created before migration 023) are visible to
        admins only, to avoid unintentionally exposing legacy data.
        """
        migration = await self.get_by_id(migration_id)
        if is_admin:
            return migration
        if migration.user_id != user_id:
            # Return 404 to avoid leaking that the migration exists
            raise MigrationNotFoundDB(f"Migration with id {migration_id} not found")
        return migration

    async def get_all(self, skip: int = 0, limit: int = 20) -> List[Migration]:
        try:
            result = await self.db.execute(
                select(Migration)
                .order_by(Migration.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return list(result.scalars().all())
        except SQLAlchemyError as e:
            raise GeneralDatabaseError(str(e)) from e

    async def get_all_for_user(
        self, user_id: str, is_admin: bool = False,
        skip: int = 0, limit: int = 20,
    ) -> List[Migration]:
        """Return migrations scoped to the caller's identity.

        Admins see everything. Analysts see only rows where user_id matches.
        """
        try:
            stmt = select(Migration).order_by(Migration.created_at.desc())
            if not is_admin:
                stmt = stmt.where(Migration.user_id == user_id)
            result = await self.db.execute(stmt.offset(skip).limit(limit))
            return list(result.scalars().all())
        except SQLAlchemyError as e:
            raise GeneralDatabaseError(str(e)) from e

    async def create(self, migration: Migration) -> Migration:
        self.db.add(migration)
        try:
            await self.db.commit()
            await self.db.refresh(migration)
            return migration
        except IntegrityError as e:
            await self.db.rollback()
            raise MigrationAlreadyExistsDB(str(e)) from e
        except SQLAlchemyError as e:
            await self.db.rollback()
            raise GeneralDatabaseError(str(e)) from e

    async def update_by_id(self, migration_id: str, data: dict) -> Migration:
        migration_to_update = await self.get_by_id(migration_id)
        for key, value in data.items():
            if hasattr(migration_to_update, key):
                setattr(migration_to_update, key, value)
        try:
            await self.db.commit()
            await self.db.refresh(migration_to_update)
            return migration_to_update
        except IntegrityError as e:
            await self.db.rollback()
            raise MigrationAlreadyExistsDB(str(e)) from e
        except SQLAlchemyError as e:
            await self.db.rollback()
            raise GeneralDatabaseError(str(e)) from e

    async def delete_by_id(self, migration_id: str) -> None:
        migration_to_delete = await self.get_by_id(migration_id)
        try:
            await self.db.delete(migration_to_delete)
            await self.db.commit()
        except SQLAlchemyError as e:
            await self.db.rollback()
            raise GeneralDatabaseError(str(e)) from e
