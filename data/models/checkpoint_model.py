"""
checkpoint_models.py - SQLAlchemy ORM models for LangGraph checkpoint + SSE events.

v008: PipelineEvent table replaces the pipeline_state.events JSON array.
      Each SSE event is a dedicated row with an integer PK — safe cursor for SSE polling.
      PipelineState is the LangGraph checkpoint: one row per migration, full state on each node.
"""
from datetime import datetime, timezone
from sqlalchemy import String, JSON, DateTime, Integer


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
from sqlalchemy.orm import Mapped, mapped_column

from configuration.database import Base


class PipelineState(Base):
    """LangGraph workflow state — one row per migration (upsert on thread_id).

    Full state JSON is replaced on every node completion by put().
    events column kept for backward-compat reads; no longer written.
    """

    __tablename__ = "pipeline_state"

    thread_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, nullable=False, index=True,
    )
    checkpoint_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True,
    )

    # LangGraph full state snapshot (replaced on every node completion)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Checkpoint metadata (phase, step, new_versions, parent_checkpoint_id)
    meta_info: Mapped[dict] = mapped_column(JSON, nullable=True)

    # Legacy event log — kept for backward-compat, no longer written.
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False,
    )

    def __repr__(self) -> str:
        return f"<PipelineState(thread_id={self.thread_id}, checkpoint_id={self.checkpoint_id})>"


class PipelineEvent(Base):
    """SSE event log — one row per event, auto-increment integer PK (FIX-2).

    Replaces: pipeline_state.events JSON array (race-condition-prone).
    The SSE router uses the integer `id` as a stable cursor for Last-Event-ID.
    """

    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    phase: Mapped[str] = mapped_column(String(50), nullable=False)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # payload holds the full event dict (details, ts, etc.)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True,
    )

    def __repr__(self) -> str:
        return f"<PipelineEvent(id={self.id}, thread_id={self.thread_id}, phase={self.phase})>"
