"""
checkpoint_events.py - Event publishing helper for LangGraph nodes.

Provides a simple interface for nodes to publish progress/events
for real-time WebSocket streaming to frontend.

USAGE IN GRAPH NODES:
    from agents.checkpoint_events import EventPublisher

    async def agent_01_node(state: MigrationState) -> MigrationState:
        thread_id = state.get("thread_id")
        pub = EventPublisher(thread_id)

        # Announce start
        await pub.phase_started("agent_01", details={"step": "analysis"})

        # ... do work ...

        # Publish progress (0-100%)
        await pub.phase_progress("agent_01", progress=50, details={"current": "scanning"})

        # More work...

        # Announce completion
        await pub.phase_completed("agent_01", details={"results": 42})

        return {"analysis_result": {...}}
"""
import logging
from typing import Optional, Any, Dict

from agents.checkpoint_postgres import get_checkpoint_saver

logger = logging.getLogger("CheckpointEvents")


class EventPublisher:
    """Helper class to publish workflow events from graph nodes."""

    def __init__(self, thread_id: str):
        """Initialize publisher for a specific migration thread.

        Args:
            thread_id: Migration ID (used in all events)
        """
        self.thread_id = thread_id
        self._saver = get_checkpoint_saver()

    async def phase_started(
        self,
        phase: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Announce that a phase has started.

        Args:
            phase: Phase name (e.g., 'agent_01', 'iac_parser')
            details: Optional context (step, description, etc.)

        Example:
            await pub.phase_started("agent_02", details={"step": "planning"})
        """
        await self._saver.publish_phase_started(
            self.thread_id,
            phase,
            details,
        )
        logger.info(f"[{self.thread_id}] Phase started: {phase}")

    async def phase_progress(
        self,
        phase: str,
        progress: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish progress update (0-100%).

        Args:
            phase: Phase name
            progress: Progress percentage (0-100)
            details: Optional context (current step, sub-step, etc.)

        Example:
            await pub.phase_progress("agent_02", progress=45, details={"substep": "resource_mapping"})
        """
        if not 0 <= progress <= 100:
            logger.warning(f"Progress must be 0-100, got {progress}")
            progress = min(100, max(0, progress))

        await self._saver.publish_phase_progress(
            self.thread_id,
            phase,
            progress,
            details,
        )
        logger.debug(f"[{self.thread_id}] {phase} progress: {progress}%")

    async def phase_completed(
        self,
        phase: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Announce that a phase has completed.

        Args:
            phase: Phase name
            details: Optional results/summary

        Example:
            await pub.phase_completed("agent_02", details={"resources_mapped": 15})
        """
        await self._saver.publish_phase_completed(
            self.thread_id,
            phase,
            details,
        )
        logger.info(f"[{self.thread_id}] Phase completed: {phase}")

    async def error(
        self,
        phase: str,
        error_message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Announce an error in a phase.

        Args:
            phase: Phase name where error occurred
            error_message: Human-readable error description
            details: Optional error context (error_type, traceback, etc.)

        Example:
            await pub.error("agent_02", "Template not found", details={"template": "storage.tf.j2"})
        """
        await self._saver.publish_error(
            self.thread_id,
            phase,
            error_message,
            details,
        )
        logger.error(f"[{self.thread_id}] Phase error: {phase} — {error_message}")

    async def human_input_required(
        self,
        migration_plan: Optional[Dict[str, Any]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit human_input_required event so the frontend can show the decision UI.

        Args:
            migration_plan: Summary data to include (resource count, preview, etc.)
            details: Optional extra context
        """
        payload = {**(details or {})}
        if migration_plan:
            payload["resource_count"] = len(migration_plan.get("resources", []))
        await self._saver._publish_event(
            self.thread_id,
            "human_input_required",
            "ask_human",
            None,
            payload,
        )
        logger.info(f"[{self.thread_id}] human_input_required emitted")

    async def custom(
        self,
        event_type: str,
        phase: str,
        progress: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish a custom event type (advanced usage).

        Args:
            event_type: Custom event type name
            phase: Phase context
            progress: Optional progress percentage
            details: Optional extra data

        Example:
            await pub.custom("checkpoint_restored", "agent_02", details={"from_checkpoint": "ckpt_001"})
        """
        await self._saver._publish_event(
            self.thread_id,
            event_type,
            phase,
            progress,
            details,
        )
        logger.debug(f"[{self.thread_id}] Custom event: {event_type} @ {phase}")
