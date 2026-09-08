"""user_repository.py — Async SQLAlchemy CRUD for User model."""
from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.user_model import User, UserRole


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Read ────────────────────────────────────────────────────────────────

    async def get_by_id(self, user_id: str) -> Optional[User]:
        result = await self._s.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self._s.execute(
            select(User).where(func.lower(User.email) == email.lower())
        )
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self._s.execute(
            select(User).where(func.lower(User.username) == username.lower())
        )
        return result.scalar_one_or_none()

    async def get_all(self, skip: int = 0, limit: int = 50) -> List[User]:
        result = await self._s.execute(
            select(User).order_by(User.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self._s.execute(select(func.count()).select_from(User))
        return result.scalar_one()

    # ── Write ───────────────────────────────────────────────────────────────

    async def create(self, user: User) -> User:
        self._s.add(user)
        await self._s.commit()
        await self._s.refresh(user)
        return user

    async def update(self, user: User, data: dict) -> User:
        for key, val in data.items():
            if hasattr(user, key) and val is not None:
                setattr(user, key, val)
        await self._s.commit()
        await self._s.refresh(user)
        return user

    async def delete(self, user: User) -> None:
        await self._s.delete(user)
        await self._s.commit()

    # ── Convenience ─────────────────────────────────────────────────────────

    async def email_taken(self, email: str, exclude_id: str | None = None) -> bool:
        q = select(User).where(func.lower(User.email) == email.lower())
        if exclude_id:
            q = q.where(User.id != exclude_id)
        result = await self._s.execute(q)
        return result.scalar_one_or_none() is not None

    async def username_taken(self, username: str, exclude_id: str | None = None) -> bool:
        q = select(User).where(func.lower(User.username) == username.lower())
        if exclude_id:
            q = q.where(User.id != exclude_id)
        result = await self._s.execute(q)
        return result.scalar_one_or_none() is not None

    async def admin_exists(self) -> bool:
        result = await self._s.execute(
            select(User).where(User.role == UserRole.ADMIN).limit(1)
        )
        return result.scalar_one_or_none() is not None
