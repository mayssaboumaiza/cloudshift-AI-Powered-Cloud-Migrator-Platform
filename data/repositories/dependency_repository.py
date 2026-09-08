"""
dependency_repository.py - Data access layer for the consolidated schema.

Consolidation (007):
- ServiceInventory table dropped → ServiceInventoryRepository reads from DependencyNode (unchanged).
- migration_priorities table dropped → MigrationPriorityRepository reads from
  migrations.migration_priority JSON column.
"""
from collections import defaultdict
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data.models.dependency_model import DependencyNode, DependencyEdge
from data.models.migration_model import Migration


class DependencyNodeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_migration(self, migration_id: str) -> List[DependencyNode]:
        result = await self.session.execute(
            select(DependencyNode)
            .where(DependencyNode.migration_id == migration_id)
            .order_by(DependencyNode.service_name)
        )
        return list(result.scalars().all())

    async def get_by_id(self, node_id: str) -> Optional[DependencyNode]:
        result = await self.session.execute(
            select(DependencyNode).where(DependencyNode.id == node_id)
        )
        return result.scalar_one_or_none()

    async def create_bulk(self, items: List[DependencyNode]) -> List[DependencyNode]:
        self.session.add_all(items)
        await self.session.commit()
        return items


class DependencyEdgeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_migration(self, migration_id: str) -> List[DependencyEdge]:
        result = await self.session.execute(
            select(DependencyEdge).where(DependencyEdge.migration_id == migration_id)
        )
        return list(result.scalars().all())

    async def create_bulk(self, items: List[DependencyEdge]) -> List[DependencyEdge]:
        self.session.add_all(items)
        await self.session.commit()
        return items


class ServiceInventoryRepository:
    """Derives the inventory view from DependencyNode rows (no dedicated table)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_migration(self, migration_id: str) -> List[dict]:
        nodes = await DependencyNodeRepository(self.session).get_by_migration(migration_id)

        bucket: dict = defaultdict(lambda: {"detection_frequency": 0, "detections_by": []})
        for n in nodes:
            key = (n.cloud_provider, n.service_name)
            meta = n.node_metadata or {}
            bucket[key]["detection_frequency"] += int(meta.get("detection_frequency") or 1)
            if meta.get("detections_by"):
                bucket[key]["detections_by"].append(str(meta["detections_by"]))
            bucket[key].setdefault("id", n.id)
            bucket[key].setdefault("created_at", n.created_at)

        return [
            {
                "id": v["id"],
                "cloud_provider": k[0],
                "service_name": k[1],
                "detection_frequency": v["detection_frequency"],
                "detections_by": ",".join(v["detections_by"]) if v["detections_by"] else None,
                "created_at": v["created_at"],
            }
            for k, v in sorted(bucket.items())
        ]


class MigrationPriorityRepository:
    """Reads migration priority from migrations.migration_priority JSON column.

    The migration_priorities table has been dropped (consolidation 007).
    Priority data is now written directly to Migration.migration_priority by DependencySaver.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_migration(self, migration_id: str) -> List[dict]:
        result = await self.session.execute(
            select(Migration.migration_priority).where(Migration.id == migration_id)
        )
        row = result.scalar_one_or_none()
        priorities: List[dict] = row or []
        # Ensure priority_order field is present (added by DependencySaver)
        return sorted(priorities, key=lambda p: p.get("priority_order", 0))

    async def create_bulk(self, migration_id: str, items: List[dict]) -> None:
        """Write priority list to migrations.migration_priority (called by DependencySaver)."""
        result = await self.session.execute(
            select(Migration).where(Migration.id == migration_id)
        )
        migration = result.scalar_one_or_none()
        if migration is not None:
            migration.migration_priority = items
            await self.session.commit()
