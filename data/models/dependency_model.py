"""
dependency_model.py - ORM models for the migration resource inventory & graph.

Consolidation (007):
- ServiceInventory dropped (was already deprecated since 002).
- MigrationPriorityItem dropped — priority list stored in migrations.migration_priority (JSON).
- DependencyNode and DependencyEdge unchanged.
"""
from datetime import datetime, timezone
from typing import Optional
import uuid

from sqlalchemy import String, DateTime, Float, ForeignKey, Text, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from configuration.database import Base

_now = lambda: datetime.now(timezone.utc)  # noqa: E731


class DependencyNode(Base):
    """One row per detected cloud resource in a migration."""

    __tablename__ = "dependency_nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    migration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("migrations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # ── Identity ──────────────────────────────────────────────────────────
    service_id: Mapped[str] = mapped_column(String(100), nullable=False)
    service_name: Mapped[str] = mapped_column(String(100), nullable=False)
    cloud_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    service_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # ── Multi-repo support ────────────────────────────────────────────────
    canonical_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    repo_origin: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)

    # ── Migration scoring ─────────────────────────────────────────────────
    risk_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="medium")
    complexity_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lock_in_level: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    effort_estimate: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # ── Free-form metadata (detection_frequency, provenance, ai_stack_hints…) ─
    node_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False,
    )

    outgoing_edges: Mapped[list["DependencyEdge"]] = relationship(
        "DependencyEdge", foreign_keys="DependencyEdge.source_node_id",
        back_populates="source_node", cascade="all, delete-orphan",
    )
    incoming_edges: Mapped[list["DependencyEdge"]] = relationship(
        "DependencyEdge", foreign_keys="DependencyEdge.target_node_id",
        back_populates="target_node", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_dependency_nodes_mig_canonical", "migration_id", "canonical_id"),
    )


class DependencyEdge(Base):
    """Directed dependency between two DependencyNode rows."""

    __tablename__ = "dependency_edges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    migration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("migrations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dependency_nodes.id", ondelete="CASCADE"), nullable=False,
    )
    target_node_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dependency_nodes.id", ondelete="CASCADE"), nullable=False,
    )

    relation_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    edge_type: Mapped[str] = mapped_column(String(30), default="depends_on", nullable=False)
    call_weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    source_node: Mapped[DependencyNode] = relationship(
        "DependencyNode", foreign_keys="DependencyEdge.source_node_id",
        back_populates="outgoing_edges",
    )
    target_node: Mapped[DependencyNode] = relationship(
        "DependencyNode", foreign_keys="DependencyEdge.target_node_id",
        back_populates="incoming_edges",
    )
