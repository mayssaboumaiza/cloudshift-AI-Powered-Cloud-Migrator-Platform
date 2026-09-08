"""
dependency_router.py - API endpoints for querying stored dependency graph data.

Provides GET endpoints to retrieve cloud service dependencies, migration priorities,
nodes, edges and graph summaries stored in PostgreSQL after Agent 02 runs.
"""
from typing import List
from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.custom_api_exceptions import NotFoundError, InternalServerError
from configuration.database import get_db_session
from data.custom_data_exceptions import MigrationNotFoundDB
from data.repositories.migration_repository import MigrationRepository
from data.repositories.dependency_repository import (
    DependencyNodeRepository,
    DependencyEdgeRepository,
    ServiceInventoryRepository,
    MigrationPriorityRepository,
)

router = APIRouter(prefix="/migrations/{migration_id}/graph", tags=["DEPENDENCY_GRAPH"])


async def _require_migration(migration_id: str, db: AsyncSession):
    """Verify migration exists or raise 404."""
    try:
        return await MigrationRepository(db).get_by_id(migration_id)
    except MigrationNotFoundDB:
        raise NotFoundError(detail=f"Migration '{migration_id}' not found")


# ─────────────────────────────────────────────────────────────────────────────
# GET /migrations/{id}/graph/services
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/services",
    response_model=List[dict],
    status_code=status.HTTP_200_OK,
    summary="Detected cloud services",
)
async def get_services(
    migration_id: str = Path(...),
    db: AsyncSession = Depends(get_db_session),
):
    """List all cloud services detected in the repository."""
    try:
        await _require_migration(migration_id, db)
        services = await ServiceInventoryRepository(db).get_by_migration(migration_id)
        # After the 2026-04-24 consolidation, services are derived from
        # DependencyNode rows and returned as plain dicts.
        return [
            {
                "id": s["id"],
                "service_name": s["service_name"],
                "cloud_provider": s["cloud_provider"],
                "detection_frequency": s["detection_frequency"],
                "created_at": s["created_at"].isoformat() if s.get("created_at") else None,
            }
            for s in services
        ]
    except NotFoundError:
        raise
    except Exception as exc:
        raise InternalServerError(detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# GET /migrations/{id}/graph/nodes
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/nodes",
    response_model=List[dict],
    status_code=status.HTTP_200_OK,
    summary="Dependency graph nodes",
)
async def get_nodes(
    migration_id: str = Path(...),
    db: AsyncSession = Depends(get_db_session),
):
    """List all service nodes in the dependency graph with risk and effort data."""
    try:
        await _require_migration(migration_id, db)
        nodes = await DependencyNodeRepository(db).get_by_migration(migration_id)
        return [
            {
                "id": n.id,
                "service_id": n.service_id,
                "service_name": n.service_name,
                "cloud_provider": n.cloud_provider,
                "service_type": n.service_type,
                "canonical_id": n.canonical_id,
                "repo_origin": n.repo_origin,
                "risk_level": n.risk_level,
                "complexity_score": n.complexity_score,
                "lock_in_level": n.lock_in_level,
                "effort_estimate": n.effort_estimate,
                "metadata": n.node_metadata or {},
                "created_at": n.created_at.isoformat(),
            }
            for n in nodes
        ]
    except NotFoundError:
        raise
    except Exception as exc:
        raise InternalServerError(detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# GET /migrations/{id}/graph/edges
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/edges",
    response_model=List[dict],
    status_code=status.HTTP_200_OK,
    summary="Dependency graph edges",
)
async def get_edges(
    migration_id: str = Path(...),
    db: AsyncSession = Depends(get_db_session),
):
    """List all dependency relationships between services."""
    try:
        await _require_migration(migration_id, db)
        edges = await DependencyEdgeRepository(db).get_by_migration(migration_id)
        return [
            {
                "id": e.id,
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "relation_type": e.relation_type,
                "edge_type": e.edge_type,
                "call_weight": e.call_weight,
                "evidence": e.evidence,
                "created_at": e.created_at.isoformat(),
            }
            for e in edges
        ]
    except NotFoundError:
        raise
    except Exception as exc:
        raise InternalServerError(detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# GET /migrations/{id}/graph/priority
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/priority",
    response_model=List[dict],
    status_code=status.HTTP_200_OK,
    summary="Migration deployment priority",
)
async def get_migration_priority(
    migration_id: str = Path(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Get the recommended deployment order (topological sort from NetworkX)."""
    try:
        await _require_migration(migration_id, db)
        priorities = await MigrationPriorityRepository(db).get_by_migration(migration_id)
        return [
            {
                "priority_order": p.priority_order,
                "service_id": p.service_id,
                "service_name": p.service_name,
                "phase": p.phase,
                "dependencies_count": p.dependencies_count,
            }
            for p in priorities
        ]
    except NotFoundError:
        raise
    except Exception as exc:
        raise InternalServerError(detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# GET /migrations/{id}/graph/summary
# ─────────────────────────────────────────────────────────────────────────────
@router.get(
    "/summary",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Dependency graph summary",
)
async def get_graph_summary(
    migration_id: str = Path(...),
    db: AsyncSession = Depends(get_db_session),
):
    """Get statistics and overview of the full dependency graph."""
    try:
        migration = await _require_migration(migration_id, db)

        nodes = await DependencyNodeRepository(db).get_by_migration(migration_id)
        edges = await DependencyEdgeRepository(db).get_by_migration(migration_id)
        services = await ServiceInventoryRepository(db).get_by_migration(migration_id)
        priorities = await MigrationPriorityRepository(db).get_by_migration(migration_id)

        # Group nodes by cloud provider and risk
        clouds: dict = {}
        risk_counts: dict = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for node in nodes:
            clouds.setdefault(node.cloud_provider, []).append(node.service_name)
            if node.risk_level in risk_counts:
                risk_counts[node.risk_level] += 1

        return {
            "migration_id": migration_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "total_services_detected": len(services),
            "migration_phases": len({p.phase for p in priorities if p.phase}),
            "clouds": clouds,
            "risk_distribution": risk_counts,
            "strategy": (migration.dependency_graph or {}).get("migration_strategy_global"),
            "total_effort": (migration.dependency_graph or {}).get("total_effort_estimate"),
            "created_at": migration.created_at.isoformat(),
            "updated_at": migration.updated_at.isoformat(),
        }
    except NotFoundError:
        raise
    except Exception as exc:
        raise InternalServerError(detail=str(exc))
