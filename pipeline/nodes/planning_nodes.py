"""
planning_nodes.py - Nœuds LangGraph de la phase de planification.

Responsabilités :
  cooldown_node      — pause entre Agent 01 et Agent 02
  check_plan_node    — valide le plan de migration produit par Agent 01
  ask_human_node     — point d'interruption — décision utilisateur
  correct_plan_node  — re-exécute Agent 01 sur les services rejetés
  export_zip_node    — exporte le plan en ZIP (chemin rejet total)
"""
import json
import logging
import os
import time
import zipfile
from pathlib import Path

from agents.pipeline_state import MigrationState
from agents.node_decorator import publish_events, _run_async_from_sync
from agents.checkpoint_events import EventPublisher
from agents.migration_planner import run_agent_01_correction
from core.constants import (
    COOLDOWN_SMALL_THRESHOLD,
    COOLDOWN_MEDIUM_THRESHOLD,
    COOLDOWN_SMALL_SLEEP,
    COOLDOWN_MEDIUM_SLEEP,
    COOLDOWN_LARGE_SLEEP,
)

logger = logging.getLogger("Graph.Planning")


# ─────────────────────────────────────────────────────────────────────────────
# cooldown_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="cooldown")
def cooldown_node(state: MigrationState) -> MigrationState:
    """Pause entre Agent 01 et Agent 02 pour éviter les rafales de requêtes LLM.

    Durée configurable via COOLDOWN_SECONDS (env var).
    La protection réelle contre le rate-limit est dans les caches TTL des outils.
    COOLDOWN_SECONDS=0 désactive le sleep entièrement (tests/dev).
    """
    dep_graph = state.get("dependency_graph") or {}
    resources_data = dep_graph.get("resources", {})
    nodes = (
        resources_data.get("nodes", [])
        if isinstance(resources_data, dict)
        else resources_data
    )

    override = os.getenv("COOLDOWN_SECONDS")
    if override is not None:
        sleep_seconds = int(override)
    else:
        n = len(nodes) if nodes else 0
        sleep_seconds = (
            COOLDOWN_SMALL_SLEEP if n <= COOLDOWN_SMALL_THRESHOLD
            else (COOLDOWN_MEDIUM_SLEEP if n <= COOLDOWN_MEDIUM_THRESHOLD
                  else COOLDOWN_LARGE_SLEEP)
        )

    if nodes and sleep_seconds > 0:
        logger.info("[Planning] Cooldown: %ds pause (%d nodes)...", sleep_seconds, len(nodes))
        time.sleep(sleep_seconds)
    else:
        logger.info("[Planning] Cooldown: skipping sleep (no resources or COOLDOWN_SECONDS=0).")
    return state


# ─────────────────────────────────────────────────────────────────────────────
# check_plan_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="check_plan")
def check_plan_node(state: MigrationState) -> MigrationState:
    """Valide le plan de migration produit par Agent 01 (planning)."""
    migration_plan = state.get("migration_plan") or {}
    artifacts = state.get("artifacts") or {}
    errors = list(state.get("errors") or [])
    warnings = list(state.get("warnings") or [])

    resources = migration_plan.get("resources", [])
    preview_report = artifacts.get("preview_report", "")

    if not resources:
        errors.append(
            "Migration plan has no resources. "
            "This may indicate an issue with cloud service detection or LLM processing."
        )
    if not preview_report:
        errors.append("Preview report is empty. Unable to display migration plan to user.")

    if resources:
        retain_count = sum(1 for r in resources if r.get("strategy") == "RETAIN")
        if retain_count == len(resources):
            warnings.append(
                f"All {len(resources)} resources have strategy RETAIN. "
                "Verify YAML mappings are complete."
            )

    if errors:
        logger.error("[Planning] check_plan: %d error(s)", len(errors))
        return {"errors": errors, "warnings": warnings}

    if warnings:
        logger.warning("[Planning] check_plan: %d warning(s)", len(warnings))

    logger.info("[Planning] check_plan: ✓ %d resources", len(resources))

    # Émettre l'événement SSE avant que le graphe se mette en pause
    thread_id = state.get("thread_id", "unknown")
    try:
        pub = EventPublisher(thread_id)
        _run_async_from_sync(
            pub.human_input_required(
                migration_plan=migration_plan,
                details={"resource_count": len(resources)},
            )
        )
    except Exception as _pub_err:
        logger.warning("[Planning] check_plan: could not emit human_input_required: %s", _pub_err)

    return {"errors": errors, "warnings": warnings}


# ─────────────────────────────────────────────────────────────────────────────
# ask_human_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="ask_human")
def ask_human_node(state: MigrationState) -> MigrationState:
    """Point d'interruption LangGraph — attend la décision utilisateur.

    Le graphe se met en pause ici (interrupt_before=["ask_human"]).
    Reprise via migration_service.accept_plan() ou reject_plan().
    """
    logger.info("[Planning] Paused at ask_human — waiting for user decision.")
    return state


# ─────────────────────────────────────────────────────────────────────────────
# correct_plan_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="correct_plan")
def correct_plan_node(state: MigrationState) -> MigrationState:
    """Re-exécute Agent 01 uniquement sur les services rejetés par l'utilisateur.

    Injecte les raisons de rejet comme contexte pour qu'Agent 01 trouve des alternatives.
    Fusionne les services corrigés avec ceux acceptés.
    Réinitialise partial_rejection pour retourner à ask_human.
    """
    rejected = state.get("rejected_services") or []
    reasons = state.get("rejection_reasons") or {}

    logger.info("[Planning] correct_plan: re-processing %d rejected service(s)", len(rejected))

    if not rejected:
        logger.warning("[Planning] correct_plan: no rejected services — skipping")
        return {"partial_rejection": False}

    try:
        result = run_agent_01_correction(
            rejected_services=rejected,
            rejection_reasons=reasons,
            accepted_plan=state.get("migration_plan") or {},
            original_constraints={
                "monthly_budget_usd": state.get("monthly_budget_usd"),
                "target_region": state.get("target_region", ""),
                "timeline": state.get("timeline", ""),
                "risk_tolerance": "medium",
            },
            state=dict(state),
        )

        corrected_plan = result.get("migration_plan", state.get("migration_plan"))
        correction_status = result.get("correction_status", "unknown")
        logger.info("[Planning] correct_plan: correction_status=%s", correction_status)

        new_count = (state.get("rejection_count") or 0) + 1

        return {
            "migration_plan": corrected_plan,
            "partial_rejection": False,
            "rejected_services": [],
            "rejection_reasons": {},
            "rejection_count": new_count,
            "artifacts": {
                **(state.get("artifacts") or {}),
                "correction_status": correction_status,
                "correction_count": new_count,
            },
            "errors": list(state.get("errors") or []),
        }

    except Exception as exc:
        logger.error("[Planning] correct_plan failed: %s", exc, exc_info=True)
        return {
            "partial_rejection": False,
            "errors": list(state.get("errors") or []) + [f"Correction failed: {exc}"],
        }


# ─────────────────────────────────────────────────────────────────────────────
# export_zip_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="export_zip")
def export_zip_node(state: MigrationState) -> MigrationState:
    """Exporte le plan de migration en ZIP (chemin de rejet total par l'utilisateur)."""
    logger.info("[Planning] User rejected — building export ZIP...")
    output_dir = Path(__file__).parent.parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = str(output_dir / "migration_plan_export.zip")

    plan = state.get("migration_plan") or {}
    preview = (state.get("artifacts") or {}).get("preview_report", "")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("migration_plan.json", json.dumps(plan, indent=2, ensure_ascii=False))
        if preview:
            zf.writestr("preview_report.md", preview)

    logger.info("[Planning] Export ZIP: %s", zip_path)
    artifacts = dict(state.get("artifacts") or {})
    artifacts["export_zip_path"] = zip_path
    return {"deployment_status": "exported", "artifacts": artifacts}
