"""Unit tests for agents/pipeline_state.py — TypedDict decomposition.

Verifies that each phase-scoped sub-TypedDict declares the expected fields
and that MigrationState inherits all of them.
"""
import pytest

from agents.pipeline_state import (
    AnalysisState,
    IaCState,
    RunnerState,
    UserInputState,
    PlanningState,
    GitHubState,
    WorkflowMeta,
    OtherState,
    MigrationState,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fields(td) -> set[str]:
    """Return the set of field names declared by a TypedDict class."""
    return set(td.__annotations__.keys())


# ─────────────────────────────────────────────────────────────────────────────
# AnalysisState
# ─────────────────────────────────────────────────────────────────────────────

def test_analysis_state_has_repo_url():
    assert "repo_url" in _fields(AnalysisState)


def test_analysis_state_has_dependency_graph():
    assert "dependency_graph" in _fields(AnalysisState)


def test_analysis_state_has_source_services_inventory():
    assert "source_services_inventory" in _fields(AnalysisState)


def test_analysis_state_has_needs_service_selection():
    assert "needs_service_selection" in _fields(AnalysisState)


# ─────────────────────────────────────────────────────────────────────────────
# IaCState
# ─────────────────────────────────────────────────────────────────────────────

def test_iac_state_has_iac_validation_success():
    assert "iac_validation_success" in _fields(IaCState)


def test_iac_state_has_correction_counts():
    assert "correction_counts" in _fields(IaCState)


def test_iac_state_has_modules_to_fix():
    assert "modules_to_fix" in _fields(IaCState)


def test_iac_state_has_security_violations():
    assert "security_violations" in _fields(IaCState)


# ─────────────────────────────────────────────────────────────────────────────
# RunnerState
# ─────────────────────────────────────────────────────────────────────────────

def test_runner_state_has_runner_job_id():
    assert "runner_job_id" in _fields(RunnerState)


def test_runner_state_has_runner_retry_count():
    assert "runner_retry_count" in _fields(RunnerState)


def test_runner_state_has_runner_failure_history():
    assert "runner_failure_history" in _fields(RunnerState)


def test_runner_state_has_runner_total_attempts():
    assert "runner_total_attempts" in _fields(RunnerState)


# ─────────────────────────────────────────────────────────────────────────────
# MigrationState inherits all sub-state fields
# ─────────────────────────────────────────────────────────────────────────────

def test_migration_state_inherits_analysis_fields():
    ms_fields = _fields(MigrationState)
    for field in ("repo_url", "dependency_graph", "source_services_inventory"):
        assert field in ms_fields, f"MigrationState missing AnalysisState field: {field}"


def test_migration_state_inherits_iac_fields():
    ms_fields = _fields(MigrationState)
    for field in ("iac_validation_success", "correction_counts"):
        assert field in ms_fields, f"MigrationState missing IaCState field: {field}"


def test_migration_state_inherits_runner_fields():
    ms_fields = _fields(MigrationState)
    for field in ("runner_job_id", "runner_retry_count", "runner_failure_history"):
        assert field in ms_fields, f"MigrationState missing RunnerState field: {field}"


def test_migration_state_inherits_workflow_meta_fields():
    ms_fields = _fields(MigrationState)
    for field in ("thread_id", "current_step", "errors", "warnings"):
        assert field in ms_fields, f"MigrationState missing WorkflowMeta field: {field}"


def test_migration_state_inherits_user_input_fields():
    ms_fields = _fields(MigrationState)
    for field in ("monthly_budget_usd", "timeline", "user_accepted"):
        assert field in ms_fields, f"MigrationState missing UserInputState field: {field}"


def test_migration_state_inherits_planning_fields():
    ms_fields = _fields(MigrationState)
    for field in ("migration_plan", "architecture_specs"):
        assert field in ms_fields, f"MigrationState missing PlanningState field: {field}"


def test_migration_state_inherits_github_fields():
    ms_fields = _fields(MigrationState)
    for field in ("github_pr_url", "github_repo_url"):
        assert field in ms_fields, f"MigrationState missing GitHubState field: {field}"


def test_migration_state_is_total_false():
    # total=False means all keys are optional — Required() is NOT in __required_keys__
    assert len(MigrationState.__required_keys__) == 0


def test_migration_state_can_be_constructed_empty():
    state: MigrationState = {}
    assert state == {}


def test_migration_state_can_be_constructed_with_subset():
    state: MigrationState = {"thread_id": "abc", "repo_url": "https://github.com/x/y"}
    assert state["thread_id"] == "abc"
    assert state["repo_url"] == "https://github.com/x/y"
