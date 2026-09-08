"""Contract tests for api/schemas/sse_events.py.

Verifies that every SSE event type serializes and deserializes correctly,
required fields are enforced, and the discriminated union rejects unknown
event types.
"""
from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from api.schemas.sse_events import (
    BaseSSEEvent,
    CompleteEvent,
    ErrorEvent,
    HumanInputEvent,
    ProgressEvent,
    RunnerPollEvent,
    ServiceSelectionEvent,
    SSEEvent,
)

_SSE_ADAPTER = TypeAdapter(SSEEvent)


# ─────────────────────────────────────────────────────────────────────────────
# ProgressEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestProgressEvent:
    def test_serializes_correctly(self):
        ev = ProgressEvent(
            event="progress",
            migration_id="mig-1",
            step="generate_iac",
            message="Agent 02 started",
        )
        d = ev.model_dump()
        assert d["event"] == "progress"
        assert d["step"] == "generate_iac"
        assert d["migration_id"] == "mig-1"

    def test_requires_step_field(self):
        with pytest.raises(ValidationError):
            ProgressEvent(event="progress", migration_id="mig-1", message="x")

    def test_requires_message_field(self):
        with pytest.raises(ValidationError):
            ProgressEvent(event="progress", migration_id="mig-1", step="x")

    def test_phase_defaults_to_empty_string(self):
        ev = ProgressEvent(
            event="progress", migration_id="mig-1", step="s", message="m"
        )
        assert ev.phase == ""

    def test_details_defaults_to_empty_dict(self):
        ev = ProgressEvent(
            event="progress", migration_id="mig-1", step="s", message="m"
        )
        assert ev.details == {}


# ─────────────────────────────────────────────────────────────────────────────
# ErrorEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorEvent:
    def test_requires_error_field(self):
        with pytest.raises(ValidationError):
            ErrorEvent(event="error", migration_id="mig-1")

    def test_valid_error_event(self):
        ev = ErrorEvent(
            event="error",
            migration_id="mig-1",
            error="Something went wrong",
        )
        assert ev.error == "Something went wrong"
        assert ev.recoverable is False

    def test_recoverable_defaults_false(self):
        ev = ErrorEvent(event="error", migration_id="m", error="e")
        assert ev.recoverable is False

    def test_recoverable_can_be_true(self):
        ev = ErrorEvent(event="error", migration_id="m", error="e", recoverable=True)
        assert ev.recoverable is True

    def test_serializes_correctly(self):
        ev = ErrorEvent(event="error", migration_id="mig-2", error="bad", recoverable=True)
        d = ev.model_dump()
        assert d["event"] == "error"
        assert d["error"] == "bad"
        assert d["recoverable"] is True


# ─────────────────────────────────────────────────────────────────────────────
# CompleteEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestCompleteEvent:
    def test_result_defaults_to_empty_dict(self):
        ev = CompleteEvent(event="complete", migration_id="mig-1")
        assert ev.result == {}

    def test_result_can_carry_data(self):
        ev = CompleteEvent(
            event="complete",
            migration_id="mig-1",
            result={"deployment_status": "deployed"},
        )
        assert ev.result["deployment_status"] == "deployed"


# ─────────────────────────────────────────────────────────────────────────────
# HumanInputEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestHumanInputEvent:
    def test_requires_prompt(self):
        with pytest.raises(ValidationError):
            HumanInputEvent(event="human_input_required", migration_id="mig-1")

    def test_options_defaults_to_none(self):
        ev = HumanInputEvent(
            event="human_input_required",
            migration_id="mig-1",
            prompt="Do you approve?",
        )
        assert ev.options is None

    def test_options_can_be_list(self):
        ev = HumanInputEvent(
            event="human_input_required",
            migration_id="mig-1",
            prompt="Do you approve?",
            options=["accept", "reject"],
        )
        assert ev.options == ["accept", "reject"]


# ─────────────────────────────────────────────────────────────────────────────
# ServiceSelectionEvent
# ─────────────────────────────────────────────────────────────────────────────

class TestServiceSelectionEvent:
    def test_services_defaults_to_empty_list(self):
        ev = ServiceSelectionEvent(
            event="service_selection_required",
            migration_id="mig-1",
        )
        assert ev.services == []

    def test_services_can_carry_data(self):
        ev = ServiceSelectionEvent(
            event="service_selection_required",
            migration_id="mig-1",
            services=[{"name": "s3", "cloud": "aws"}],
        )
        assert len(ev.services) == 1


# ─────────────────────────────────────────────────────────────────────────────
# SSEEvent discriminated union
# ─────────────────────────────────────────────────────────────────────────────

class TestSSEUnion:
    def test_routes_progress_event(self):
        data = {
            "event": "progress",
            "migration_id": "m",
            "step": "build_plan",
            "message": "planning...",
        }
        ev = _SSE_ADAPTER.validate_python(data)
        assert isinstance(ev, ProgressEvent)

    def test_routes_error_event(self):
        data = {"event": "error", "migration_id": "m", "error": "oops"}
        ev = _SSE_ADAPTER.validate_python(data)
        assert isinstance(ev, ErrorEvent)

    def test_routes_complete_event(self):
        data = {"event": "complete", "migration_id": "m"}
        ev = _SSE_ADAPTER.validate_python(data)
        assert isinstance(ev, CompleteEvent)

    def test_routes_human_input_event(self):
        data = {"event": "human_input_required", "migration_id": "m", "prompt": "approve?"}
        ev = _SSE_ADAPTER.validate_python(data)
        assert isinstance(ev, HumanInputEvent)

    def test_routes_service_selection_event(self):
        data = {"event": "service_selection_required", "migration_id": "m"}
        ev = _SSE_ADAPTER.validate_python(data)
        assert isinstance(ev, ServiceSelectionEvent)

    def test_routes_runner_poll_event(self):
        data = {"event": "runner_poll", "migration_id": "m", "job_id": "j1", "runner_status": "running"}
        ev = _SSE_ADAPTER.validate_python(data)
        assert isinstance(ev, RunnerPollEvent)

    def test_unknown_event_type_is_rejected(self):
        data = {"event": "unknown_type", "migration_id": "m"}
        with pytest.raises(ValidationError):
            _SSE_ADAPTER.validate_python(data)

    def test_missing_migration_id_is_rejected(self):
        data = {"event": "complete"}
        with pytest.raises(ValidationError):
            _SSE_ADAPTER.validate_python(data)
