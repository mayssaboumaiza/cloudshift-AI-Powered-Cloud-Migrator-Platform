"""
checkpoint_postgres.py - PostgreSQL-backed checkpoint saver for LangGraph.

v008: SSE events → pipeline_events table (INSERT per event, integer PK cursor).
Eliminates the race condition caused by full-row upserts overwriting the
pipeline_state.events JSON array.

Checkpoint strategy: full state persisted on every node completion (put).
put_writes is intentionally a no-op — the full checkpoint after each node is
the single source of truth; partial mid-node writes add complexity for marginal gain.
"""
import json
import logging
from datetime import datetime, timezone
import asyncio
from typing import Optional, Any, Dict, Iterator, AsyncIterator, Sequence

from langgraph.checkpoint.base import BaseCheckpointSaver, Checkpoint, CheckpointTuple, RunnableConfig
from sqlalchemy import select, desc, create_engine
from sqlalchemy.orm import Session

from configuration.settings import db_settings
from data.models.checkpoint_model import PipelineState, PipelineEvent

logger = logging.getLogger("PostgresCheckpointSaver")


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that handles all LangChain/LangGraph non-serializable types."""

    def default(self, obj: Any) -> Any:
        from datetime import date
        # datetime / date
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        # Pydantic v2
        if hasattr(obj, 'model_dump'):
            try:
                return obj.model_dump()
            except Exception:
                pass
        # Pydantic v1
        if hasattr(obj, 'dict'):
            try:
                return obj.dict()
            except Exception:
                pass
        # Generic object with __dict__
        if hasattr(obj, '__dict__'):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
        return str(obj)


def _normalize_for_json(obj: Any) -> Any:
    """Serialize *obj* to a plain JSON-compatible dict/list via round-trip.

    Using json.dumps with a custom encoder guarantees the result is always
    serializable — no matter how deeply nested LangChain message objects are.
    """
    return json.loads(json.dumps(obj, cls=_SafeEncoder))


class PostgresCheckpointSaver(BaseCheckpointSaver):
    """
    LangGraph checkpoint saver backed by PostgreSQL.

    Features:
    - Sync checkpoint save/load (no event loop issues)
    - SSE events: INSERT into pipeline_events table (one row per event, integer PK cursor)
    - LangGraph state: one row per migration in pipeline_state (upsert on thread_id)
    """

    def __init__(self):
        sync_url = db_settings.POSTGRES_URI.replace("postgresql+asyncpg://", "postgresql://")
        self.engine = create_engine(sync_url, echo=False, pool_size=5, max_overflow=10)
        logger.info("PostgresCheckpointSaver initialized")

    # ─────────────────────────────────────────────────────────────────────────
    # LangGraph interface
    # ─────────────────────────────────────────────────────────────────────────

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: Dict[str, Any],
        new_versions: Dict[str, Any],
    ) -> RunnableConfig:
        try:
            thread_id = config.get("configurable", {}).get("thread_id")
            if not thread_id:
                logger.warning("No thread_id in config, skipping checkpoint")
                return config

            configurable = dict(config.get("configurable", {}))
            checkpoint_id = checkpoint.get("id") or f"ckpt_{datetime.now(timezone.utc).isoformat()}"
            parent_checkpoint_id = configurable.get("checkpoint_id")

            checkpoint_data = _normalize_for_json({
                **checkpoint,
                "id": checkpoint_id,
            })

            with Session(self.engine) as session:
                existing = session.scalar(
                    select(PipelineState).where(PipelineState.thread_id == thread_id)
                )
                meta = _normalize_for_json({
                    **(metadata or {}),
                    "new_versions": new_versions,
                    "parent_checkpoint_id": parent_checkpoint_id,
                })
                if existing:
                    existing.checkpoint = checkpoint_data
                    existing.checkpoint_id = checkpoint_id
                    existing.meta_info = meta
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    session.add(PipelineState(
                        thread_id=thread_id,
                        checkpoint_id=checkpoint_id,
                        checkpoint=checkpoint_data,
                        meta_info=meta,
                        events=[],
                    ))
                session.commit()
            logger.debug(f"Checkpoint saved: thread_id={thread_id}")
            return {"configurable": {**configurable, "thread_id": thread_id, "checkpoint_id": checkpoint_id}}
        except Exception as exc:
            logger.error(f"Error saving checkpoint: {exc}", exc_info=True)
            return config

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        try:
            configurable = config.get("configurable", {})
            thread_id = configurable.get("thread_id")
            checkpoint_id = configurable.get("checkpoint_id")
            if not thread_id:
                return None

            with Session(self.engine) as session:
                stmt = select(PipelineState).where(PipelineState.thread_id == thread_id)
                if checkpoint_id:
                    stmt = stmt.where(PipelineState.checkpoint_id == checkpoint_id)
                else:
                    stmt = stmt.order_by(desc(PipelineState.updated_at))

                row = session.scalar(stmt)
                if row:
                    return CheckpointTuple(
                        config={"configurable": {**configurable, "thread_id": thread_id, "checkpoint_id": row.checkpoint_id}},
                        checkpoint=row.checkpoint,
                        metadata=row.meta_info or {},
                        parent_config=None,
                        pending_writes=[],
                    )
            return None
        except Exception as exc:
            logger.error(f"Error retrieving checkpoint: {exc}", exc_info=True)
            return None

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """No-op — the full checkpoint saved by put() is the single source of truth.

        Mid-node partial writes add DB load and complexity for marginal crash-recovery
        gain in a sequential single-migration pipeline.  Recovery resumes from the
        last completed node checkpoint, which is acceptable.
        """
        logger.debug("put_writes: %d write(s) for task_id=%s (not persisted)", len(writes), task_id)

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        del before
        thread_id = (config or {}).get("configurable", {}).get("thread_id")
        if not thread_id:
            return iter(())
        cp_tuple = self.get_tuple(config or {"configurable": {"thread_id": thread_id}})
        if cp_tuple is None:
            return iter(())
        if filter:
            metadata = cp_tuple.metadata or {}
            if any(metadata.get(k) != v for k, v in filter.items()):
                return iter(())
        if limit == 0:
            return iter(())
        return iter([cp_tuple])

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None) -> AsyncIterator[CheckpointTuple]:
        items = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in items:
            yield item

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    # ─────────────────────────────────────────────────────────────────────────
    # SSE event publishing — INSERT into pipeline_events table (FIX-2: one row per event, integer PK cursor)
    # ─────────────────────────────────────────────────────────────────────────

    async def publish_phase_started(self, thread_id: str, phase: str, details: Optional[Dict] = None) -> None:
        await self._publish_event(thread_id, "phase_started", phase, details=details or {})

    async def publish_phase_progress(self, thread_id: str, phase: str, progress: int, details: Optional[Dict] = None) -> None:
        await self._publish_event(thread_id, "phase_progress", phase, progress=progress, details=details or {})

    async def publish_phase_completed(self, thread_id: str, phase: str, details: Optional[Dict] = None) -> None:
        await self._publish_event(thread_id, "phase_completed", phase, details=details or {})

    async def publish_error(self, thread_id: str, phase: str, error_message: str, details: Optional[Dict] = None) -> None:
        await self._publish_event(thread_id, "error_occurred", phase, details={**(details or {}), "error_message": error_message})

    async def _publish_event(
        self,
        thread_id: str,
        event_type: str,
        phase: str,
        progress: Optional[int] = None,
        details: Optional[Dict] = None,
    ) -> None:
        """FIX-2: INSERT a new row into pipeline_events (one row per event).

        Eliminates the race condition where full-row upserts on pipeline_state
        overwrote the events JSON array.  Each event now has a stable integer PK
        that the SSE router uses as a cursor (Last-Event-ID).
        """
        try:
            payload = {
                "details": details or {},
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            with Session(self.engine) as session:
                session.add(PipelineEvent(
                    thread_id=thread_id,
                    event_type=event_type,
                    phase=phase,
                    progress=progress,
                    payload=payload,
                ))
                session.commit()
            logger.debug(
                "Event inserted: thread_id=%s event_type=%s phase=%s",
                thread_id, event_type, phase,
            )
        except Exception as exc:
            logger.warning("Event publishing failed (non-critical): %s", exc)

    async def get_events_stream(
        self,
        thread_id: str,
        after_index: Optional[int] = None,
    ):
        """FIX-2: Return events from pipeline_events table with id > after_index.

        Uses the integer PK as a stable, monotonic cursor — safe for concurrent
        inserts (no row-level locking needed).  The SSE router passes the last
        seen id as after_index so the stream resumes without duplicates.

        Yields: dicts with id, event_type, phase, progress, details, ts
        """
        try:
            with Session(self.engine) as session:
                stmt = (
                    select(PipelineEvent)
                    .where(PipelineEvent.thread_id == thread_id)
                )
                if after_index is not None:
                    stmt = stmt.where(PipelineEvent.id > after_index)
                stmt = stmt.order_by(PipelineEvent.id)

                rows = session.scalars(stmt).all()
                for row in rows:
                    payload = row.payload or {}
                    yield {
                        "id":         row.id,
                        "event_type": row.event_type,
                        "phase":      row.phase,
                        "progress":   row.progress,
                        "details":    payload.get("details", {}),
                        "ts":         payload.get("ts", row.created_at.isoformat()),
                    }
        except Exception as exc:
            logger.error("Error fetching events: %s", exc, exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_saver_instance: Optional[PostgresCheckpointSaver] = None


def get_checkpoint_saver() -> PostgresCheckpointSaver:
    global _saver_instance
    if _saver_instance is None:
        _saver_instance = PostgresCheckpointSaver()
        logger.info("PostgresCheckpointSaver singleton initialized")
    return _saver_instance
