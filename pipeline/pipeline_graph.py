"""
pipeline_graph.py — LangGraph workflow definition for Cloud Migrator.

This file declares only the StateGraph structure (nodes + edges).
All business logic lives in the sub-modules:
  agents/nodes/    — node functions per phase
  agents/routers/  — conditional routing functions

Pipeline:
  analyze_repo → check_analysis → cooldown → build_plan → check_plan
    → ask_human → generate_iac → validate_intent → validate_iac
    → deploy → enqueue_deploy → wait_runner → health_check
    → publish_github → END
  ask_human(reject) → export_zip → END

Deployment flow:
  1. executor fetches credentials JIT from Vault (per-migration, never in .env)
  2. terraform plan -out=tfplan → human approval via SSE → terraform apply tfplan
  3. publish_github pushes .tf + github_actions.yml to GitHub PR for audit trail
"""
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))

from langgraph.graph import StateGraph, END

from agents.pipeline_state import MigrationState
from agents.node_decorator import publish_events
from agents.checkpoint_postgres import get_checkpoint_saver

# ── Agents (external functions wrapped in nodes) ─────────────────────────────
from services.stack_analyzer import run_iac_parser
from agents.migration_planner import run_agent_01, run_agent_01_correction  # noqa: F401
from agents.iac_generator import run_agent_02, run_agent_02_fix, Agent02IaCValidator  # noqa: F401
from agents.deployer import run_agent_03

# ── LangGraph nodes ──────────────────────────────────────────────────────────
from pipeline.nodes import (
    # Phase 0 — Change detection (Path 1 vs Path 2)
    detect_changes_node,
    # Phase 0 — Analysis
    check_analysis_node,
    notify_service_selection_node,
    ask_user_services_node,
    # Phase 1 — Planning
    cooldown_node,
    check_plan_node,
    ask_human_node,
    correct_plan_node,
    export_zip_node,
    # Phase 2 — IaC
    validate_intent_node,
    validate_iac_node,
    # Phase 3 — Deploy (executor queue + TerraformRunner)
    enqueue_deploy_node,
    wait_runner_node,
    mark_runner_regen_node,
    mark_runner_retry_node,
    # Phase 4 — Health check + GitHub publish
    health_check_node,
    publish_github_node,
)

# ── Conditional routers ───────────────────────────────────────────────────────
from pipeline.routers import (
    route_after_analysis,
    route_check_plan,
    route_human_decision,
    route_intent_validation,
    route_validation,
    route_runner_result,
    route_after_health,
)


# ─────────────────────────────────────────────────────────────────────────────
# StateGraph construction
# ─────────────────────────────────────────────────────────────────────────────
workflow = StateGraph(MigrationState)

# ── Nodes ─────────────────────────────────────────────────────────────────────
workflow.add_node("detect_changes",           detect_changes_node)
workflow.add_node("analyze_repo",             publish_events(phase="iac_parser")(run_iac_parser))
workflow.add_node("check_analysis",           check_analysis_node)
workflow.add_node("notify_service_selection", notify_service_selection_node)
workflow.add_node("ask_user_services",        ask_user_services_node)
workflow.add_node("cooldown",                 cooldown_node)
workflow.add_node("build_plan",               publish_events(phase="agent_01")(run_agent_01))
workflow.add_node("check_plan",               check_plan_node)
workflow.add_node("ask_human",                ask_human_node)
workflow.add_node("correct_plan",             correct_plan_node)
workflow.add_node("generate_iac",             publish_events(phase="agent_02")(run_agent_02))
workflow.add_node("validate_intent",          validate_intent_node)
workflow.add_node("validate_iac",             validate_iac_node)
workflow.add_node("fix_targeted",             publish_events(phase="agent_02_fix")(run_agent_02_fix))
workflow.add_node("deploy",                   publish_events(phase="agent_03")(run_agent_03))
workflow.add_node("enqueue_deploy",           enqueue_deploy_node)
workflow.add_node("wait_runner",              wait_runner_node)
workflow.add_node("mark_runner_regen",        mark_runner_regen_node)
workflow.add_node("mark_runner_retry",        mark_runner_retry_node)
workflow.add_node("health_check",             health_check_node)
workflow.add_node("publish_github",           publish_github_node)
workflow.add_node("export_zip",               export_zip_node)

# ── Entry point ───────────────────────────────────────────────────────────────
workflow.set_entry_point("detect_changes")

# ── Phase 0a: change detection ────────────────────────────────────────────────
# mode == "up_to_date"  → END (skip everything — repo unchanged)
# mode == "full"        → analyze_repo (Path 1 — full generation)
# mode == "incremental" → analyze_repo (Path 2 — delta generation)
workflow.add_conditional_edges(
    "detect_changes",
    lambda s: "end" if s.get("migration_mode") == "up_to_date" else "analyze_repo",
    {"end": END, "analyze_repo": "analyze_repo"},
)

# ── Phase 0b: repository analysis ─────────────────────────────────────────────
# IaC found   → check_analysis → cooldown → build_plan
# No IaC      → notify_service_selection → [interrupt] ask_user_services → cooldown
# Error        → END
workflow.add_edge("analyze_repo", "check_analysis")
workflow.add_conditional_edges(
    "check_analysis",
    route_after_analysis,
    {"cooldown": "cooldown", "abort": END, "select_services": "notify_service_selection"},
)
workflow.add_edge("notify_service_selection", "ask_user_services")
workflow.add_conditional_edges(
    "ask_user_services",
    lambda state: "abort" if state.get("errors") else "cooldown",
    {"cooldown": "cooldown", "abort": END},
)

# ── Phase 1: planning ─────────────────────────────────────────────────────────
workflow.add_edge("cooldown", "build_plan")
workflow.add_edge("build_plan", "check_plan")
workflow.add_conditional_edges(
    "check_plan",
    route_check_plan,
    {"continue": "ask_human", "abort": END},
)

# ── Human decision ────────────────────────────────────────────────────────────
workflow.add_conditional_edges(
    "ask_human",
    route_human_decision,
    {"generate": "generate_iac", "correct": "correct_plan", "reject": "export_zip"},
)

# Partial rejection loop → back to ask_human via check_plan
workflow.add_edge("correct_plan", "check_plan")

# ── Phase 2: IaC generation → intent validation → technical validation ────────
# Order: validate_intent first (business constraints) → validate_iac (syntax/security)
# Rationale: fail-fast on business violations before running Terraform/Checkov
workflow.add_edge("generate_iac", "validate_intent")
workflow.add_conditional_edges(
    "validate_intent",
    route_intent_validation,
    {"regen": "generate_iac", "continue": "validate_iac"},
)
workflow.add_conditional_edges(
    "validate_iac",
    route_validation,
    {
        "intent":         "deploy",
        "fix":            "fix_targeted",
        "security_regen": "generate_iac",
        "escalate":       END,
    },
)

# IaC correction loop
workflow.add_edge("fix_targeted", "validate_iac")

# ── Phase 3: deploy (Agent 03 artifacts) → enqueue → wait (interrupt) ─────────
# Agent 03 generates deploy.sh + CI/CD YAML (Jinja2).
# enqueue_deploy copies IaC to per-migration work_dir and submits a RunnerJob.
# wait_runner is interrupted here; the graph resumes when the executor callback
# injects the terminal job status into the state.
workflow.add_edge("deploy", "enqueue_deploy")
workflow.add_edge("enqueue_deploy", "wait_runner")
workflow.add_conditional_edges(
    "wait_runner",
    route_runner_result,
    {
        "success": "health_check",
        "regen":   "mark_runner_regen",
        "retry":   "mark_runner_retry",
        "escalate": END,
    },
)

# Runner failure loops
workflow.add_edge("mark_runner_regen", "generate_iac")   # IaC codegen error → regenerate
workflow.add_edge("mark_runner_retry", "enqueue_deploy")  # transient error → re-enqueue

# ── Phase 4: health check → publish GitHub PR ─────────────────────────────────
# health_check: 6 deterministic post-deployment checks (no LLM)
# publish_github: creates GitHub repo, pushes .tf files, opens PR for audit trail
workflow.add_conditional_edges(
    "health_check",
    route_after_health,
    {"publish": "publish_github", "halt": END},
)
workflow.add_edge("publish_github", END)

# ── Rejection branch ──────────────────────────────────────────────────────────
workflow.add_edge("export_zip", END)

# ─────────────────────────────────────────────────────────────────────────────
# Lazy compilation — call get_compiled_graph() instead of importing `app` directly.
# The graph is compiled once on first access (inside the FastAPI lifespan hook)
# so the PostgreSQL checkpointer is not created at module import time.
#
# Thread-safety: _graph_lock prevents a TOCTOU race under multi-worker deployments.
# Without the lock, two concurrent requests can both pass the `is None` check and
# create two separate compiled graphs with two separate PostgresCheckpointer
# connections — the second overwrite leaks the first checkpointer connection and
# silently loses in-flight checkpoints.
# ─────────────────────────────────────────────────────────────────────────────
_compiled_graph = None
_graph_lock = threading.Lock()


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        with _graph_lock:
            # Re-check inside the lock — another thread may have compiled while
            # we were waiting to acquire it.
            if _compiled_graph is None:
                _compiled_graph = workflow.compile(
                    checkpointer=get_checkpoint_saver(),
                    interrupt_before=["ask_human", "ask_user_services", "wait_runner"],
                )
    return _compiled_graph
