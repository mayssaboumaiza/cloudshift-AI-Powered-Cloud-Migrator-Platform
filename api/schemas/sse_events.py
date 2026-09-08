"""
sse_events.py — Pydantic models for every SSE event type emitted by the pipeline.

All events share a common BaseSSEEvent and are identified by the `event` field.
Use the SSEEvent discriminated union for generic deserialization.

Emission reference:
  ProgressEvent           → agents/node_decorator.py (publish_events wrapper)
  ServiceSelectionEvent   → agents/nodes/analysis_nodes.py (notify_service_selection_node)
  HumanInputEvent         → agents/nodes/planning_nodes.py (ask_human_node)
  RunnerPollEvent         → agents/nodes/deploy_nodes.py (wait_runner_node)
  CompleteEvent           → api/routers/v1/sse_router.py (stream end)
  ErrorEvent              → api/routers/v1/sse_router.py (stream error path)
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────────────────────

class BaseSSEEvent(BaseModel):
    """Fields present on every SSE event."""
    event: str
    migration_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Concrete event types
# ─────────────────────────────────────────────────────────────────────────────

class ProgressEvent(BaseSSEEvent):
    """Emitted by publish_events() on each LangGraph node entry/exit."""
    event: Literal["progress"]
    step: str
    message: str
    phase: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ServiceSelectionEvent(BaseSSEEvent):
    """Emitted when the stack analyzer finds no IaC and needs manual selection."""
    event: Literal["service_selection_required"]
    services: list[dict[str, Any]] = Field(default_factory=list)


class HumanInputEvent(BaseSSEEvent):
    """Emitted by ask_human_node — the pipeline is paused waiting for user approval."""
    event: Literal["human_input_required"]
    prompt: str
    options: list[str] | None = None
    migration_plan_preview: dict[str, Any] | None = None


class RunnerPollEvent(BaseSSEEvent):
    """Emitted periodically by wait_runner_node with the runner job's current status."""
    event: Literal["runner_poll"]
    job_id: str
    runner_status: str
    elapsed_seconds: float = 0.0


class CompleteEvent(BaseSSEEvent):
    """Emitted when the pipeline finishes successfully."""
    event: Literal["complete"]
    result: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(BaseSSEEvent):
    """Emitted on pipeline errors or SSE stream failures."""
    event: Literal["error"]
    error: str
    recoverable: bool = False
    detail: dict[str, Any] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Discriminated union — use for generic deserialization
# ─────────────────────────────────────────────────────────────────────────────

SSEEvent = Annotated[
    Union[
        ProgressEvent,
        ServiceSelectionEvent,
        HumanInputEvent,
        RunnerPollEvent,
        CompleteEvent,
        ErrorEvent,
    ],
    Field(discriminator="event"),
]
