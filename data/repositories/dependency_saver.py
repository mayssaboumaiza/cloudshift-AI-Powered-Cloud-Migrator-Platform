"""
dependency_saver.py - Persists Agent 02 results to consolidated PostgreSQL tables.

Consolidation (007):
- migration_priorities table dropped → priorities written to migrations.migration_priority JSON.
- ServiceInventory table dropped → detection info merged into DependencyNode.metadata.
"""
import hashlib
import logging
from typing import Any, Dict, List

from sqlalchemy.ext.asyncio import AsyncSession

from data.models.dependency_model import DependencyEdge, DependencyNode
from data.repositories.dependency_repository import (
    DependencyEdgeRepository,
    DependencyNodeRepository,
    MigrationPriorityRepository,
)

logger = logging.getLogger("DependencySaver")


def _canonical_id(provider: str, service_type: str, logical_name: str) -> str:
    key = f"{(provider or '').lower()}:{(service_type or '').lower()}:{(logical_name or '').lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _index_inventory(source_services_inventory: List[Dict[str, Any]]) -> Dict[str, Dict]:
    index: Dict[str, Dict] = {}
    for s in source_services_inventory or []:
        key = f"{s.get('cloud_provider', '').lower()}:{s.get('service_name', '').lower()}"
        index[key] = {
            "detection_frequency": s.get("detection_frequency", 1),
            "detections_by": s.get("detections_by"),
        }
    return index


class DependencySaver:
    """Persists the dependency graph into the consolidated schema."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_dependency_graph(
        self,
        migration_id: str,
        dependency_graph: Dict[str, Any],
        source_services_inventory: List[Dict[str, Any]],
        migration_priority: List[Dict[str, Any]],
        repo_origin: str | None = None,
    ) -> None:
        nodes_list = (dependency_graph.get("resources") or {}).get("nodes", [])
        edges_list = (dependency_graph.get("resources") or {}).get("edges", [])

        inventory_index = _index_inventory(source_services_inventory)

        # Save nodes and get back the service_id → db_uuid mapping for edge resolution
        service_id_to_uuid = await self._save_nodes(
            migration_id, nodes_list, inventory_index, repo_origin
        )
        await self._save_edges(migration_id, edges_list, service_id_to_uuid)
        await self._save_priority(migration_id, migration_priority)

        logger.info(
            "Saved graph for %s: %d nodes, %d edges, %d priorities",
            migration_id, len(nodes_list), len(edges_list), len(migration_priority),
        )

    async def _save_nodes(
        self,
        migration_id: str,
        nodes: List[Dict],
        inventory_index: Dict[str, Dict],
        repo_origin: str | None,
    ) -> Dict[str, str]:
        """Save nodes and return a mapping of graph node id → database UUID."""
        if not nodes:
            return {}

        items: list[DependencyNode] = []
        for n in nodes:
            provider = n.get("cloud_provider", "")
            name = n.get("name", "")
            service_type = n.get("service_type") or ""

            inv = inventory_index.get(f"{provider.lower()}:{name.lower()}", {})
            metadata = dict(n.get("metadata") or {})
            if inv:
                metadata["detection_frequency"] = inv.get("detection_frequency")
                metadata["detections_by"] = inv.get("detections_by")
            if n.get("external_handles"):
                metadata["external_handles"] = n["external_handles"]
            if n.get("provenance"):
                metadata["provenance"] = n["provenance"]

            items.append(
                DependencyNode(
                    migration_id=migration_id,
                    service_id=n.get("id", ""),
                    service_name=name,
                    cloud_provider=provider,
                    service_type=service_type,
                    canonical_id=n.get("canonical_id") or _canonical_id(provider, service_type, name),
                    repo_origin=n.get("repo_origin") or repo_origin,
                    risk_level=n.get("risk_level", "medium"),
                    complexity_score=n.get("complexity_score"),
                    lock_in_level=n.get("lock_in_level"),
                    effort_estimate=n.get("effort_estimate"),
                    node_metadata=metadata or None,
                )
            )

        saved = await DependencyNodeRepository(self.session).create_bulk(items)

        # Build service_id → db UUID mapping so edges can reference real PKs
        service_id_to_uuid: Dict[str, str] = {}
        for node_obj, raw_node in zip(saved, nodes):
            graph_id = raw_node.get("id", "")
            if graph_id and node_obj.id:
                service_id_to_uuid[graph_id] = str(node_obj.id)

        return service_id_to_uuid

    async def _save_edges(
        self,
        migration_id: str,
        edges: List[Dict],
        service_id_to_uuid: Dict[str, str],
    ) -> None:
        if not edges:
            return

        items = []
        skipped = 0
        for e in edges:
            src_graph_id = e.get("source", "")
            tgt_graph_id = e.get("target", "")

            # Skip edges with empty source or target
            if not src_graph_id or not tgt_graph_id:
                skipped += 1
                continue

            # Resolve graph node IDs → database UUIDs
            src_uuid = service_id_to_uuid.get(src_graph_id)
            tgt_uuid = service_id_to_uuid.get(tgt_graph_id)

            if not src_uuid or not tgt_uuid:
                skipped += 1
                logger.debug(
                    "_save_edges: no DB node for edge %s → %s — skipped",
                    src_graph_id, tgt_graph_id,
                )
                continue

            items.append(
                DependencyEdge(
                    migration_id=migration_id,
                    source_node_id=src_uuid,
                    target_node_id=tgt_uuid,
                    relation_type=e.get("relation_type"),
                    edge_type=e.get("edge_type", "depends_on"),
                    call_weight=float(e.get("weight", 1.0)),
                    evidence=e.get("evidence"),
                )
            )

        if skipped:
            logger.warning("_save_edges: skipped %d edge(s) with unresolvable source/target", skipped)
        if items:
            await DependencyEdgeRepository(self.session).create_bulk(items)

    async def _save_priority(self, migration_id: str, priorities: List[Dict]) -> None:
        if not priorities:
            return
        items = [
            {
                "priority_order": idx,
                "service_id": p.get("service_id", ""),
                "service_name": p.get("service_name", ""),
                "phase": p.get("phase"),
                "dependencies_count": p.get("dependencies_count", 0),
            }
            for idx, p in enumerate(priorities, start=1)
        ]
        await MigrationPriorityRepository(self.session).create_bulk(migration_id, items)
