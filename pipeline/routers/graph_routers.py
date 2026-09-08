"""
graph_routers.py - Fonctions de routage conditionnel du workflow LangGraph.

Chaque fonction route_* prend l'état courant et retourne une clé de chaîne
qui sélectionne l'arête conditionnelle dans le StateGraph.

Convention de nommage :
  route_after_<node>  — routeur sortant d'un nœud spécifique
  route_<condition>   — routeur basé sur une condition métier

Toutes sont synchrones sauf les appels à resolve_canonical_state (importé en ligne).
"""
import logging

from agents.pipeline_state import MigrationState
from core.constants import (
    MAX_REJECTION_COUNT,
    MAX_RUNNER_TOTAL_ATTEMPTS,
    MAX_RUNNER_REGEN_COUNT,
    MAX_RUNNER_RETRY_COUNT,
    MAX_RUNNER_SIG_REPEATS,
    MAX_INTENT_REGEN,
)

logger = logging.getLogger("Graph.Routers")


# ─────────────────────────────────────────────────────────────────────────────
# route_after_analysis
# ─────────────────────────────────────────────────────────────────────────────

def route_after_analysis(state: MigrationState) -> str:
    """Route depuis analyze_repo.

    - errors         → abort (END)
    - needs_selection → notify_service_selection (chemin checklist)
    - normal          → cooldown (chemin IaC)
    """
    if state.get("errors"):
        logger.error("[Router] analyze_repo errors — aborting pipeline")
        return "abort"
    if state.get("needs_service_selection") or (
        state.get("dependency_graph", {}).get("needs_service_selection")
    ):
        logger.info("[Router] No IaC found — routing to service selection checklist")
        return "select_services"
    return "cooldown"


# ─────────────────────────────────────────────────────────────────────────────
# route_check_plan
# ─────────────────────────────────────────────────────────────────────────────

def route_check_plan(state: MigrationState) -> str:
    if state.get("errors"):
        logger.error("[Router] check_plan errors — aborting pipeline")
        return "abort"
    return "continue"


# ─────────────────────────────────────────────────────────────────────────────
# route_human_decision
# ─────────────────────────────────────────────────────────────────────────────

def route_human_decision(state: MigrationState) -> str:
    if state.get("user_accepted"):
        logger.info("[Router] User accepted → generate_iac")
        return "generate"
    if state.get("partial_rejection"):
        if (state.get("rejection_count") or 0) >= MAX_REJECTION_COUNT:
            logger.warning("[Router] Max correction cycles (%d) reached → export_zip", MAX_REJECTION_COUNT)
            return "reject"
        logger.info("[Router] Partial rejection → correct_plan")
        return "correct"
    logger.info("[Router] Full rejection → export_zip")
    return "reject"


# ─────────────────────────────────────────────────────────────────────────────
# route_intent_validation
# ─────────────────────────────────────────────────────────────────────────────

def route_intent_validation(state: MigrationState) -> str:
    """Route après validate_intent.

    Déclenche une re-génération si l'un des issues critiques est détecté et
    que le quota de re-générations n'est pas épuisé.

    Issues critiques (trigger regen):
      - "Region mismatch"          — région incorrecte
      - "Budget exceeded"          — SKU trop cher vs budget déclaré
      - "Production intent"        — backup_retention_days trop bas pour prod
      - "Security intent"          — TLS version incorrecte
      - "Hardcoded secret"         — secret en clair dans le HCL

    Tous les autres cas (warning non-bloquant, quota atteint) → validate_iac.
    """
    intent_issues = state.get("intent_issues") or []
    regen_count = state.get("iac_regen_count") or 0

    _CRITICAL_PREFIXES = (
        "Region mismatch",
        "Budget exceeded",
        "Production intent violation",
        "Security intent violation",
        "Hardcoded secret",
    )
    critical = any(
        any(issue.startswith(prefix) for prefix in _CRITICAL_PREFIXES)
        for issue in intent_issues
    )

    if critical and regen_count < MAX_INTENT_REGEN:
        triggered_by = next(
            (issue[:60] for issue in intent_issues
             if any(issue.startswith(p) for p in _CRITICAL_PREFIXES)),
            "unknown",
        )
        logger.warning(
            "[Router] validate_intent: critical issue → re-running generate_iac "
            "(attempt %d/%d): %s...",
            regen_count + 1, MAX_INTENT_REGEN, triggered_by,
        )
        return "regen"

    if critical and regen_count >= MAX_INTENT_REGEN:
        logger.warning(
            "[Router] validate_intent: intent issues persist after %d regen — "
            "continuing to validate_iac (will surface as warning)",
            MAX_INTENT_REGEN,
        )

    return "continue"


# ─────────────────────────────────────────────────────────────────────────────
# route_validation_iac
# ─────────────────────────────────────────────────────────────────────────────

def route_validation(state: MigrationState) -> str:
    """Route après validate_iac.

    Priorité : escalade > succès > security_regen > fix > escalade (pas de modules).
    """
    if state.get("needs_human_escalation"):
        logger.warning("[Router] Human escalation flagged → escalate")
        return "escalate"

    if state.get("iac_validation_success"):
        creds_validated = state.get("credentials_pre_validated", False)
        if creds_validated:
            logger.info("[Router] IaC validation SUCCESS, credentials pre-validated → deploy")
        else:
            logger.warning(
                "[Router] IaC validation SUCCESS but credentials_pre_validated=False — "
                "deploy node will generate scripts only; terraform apply will be SKIPPED."
            )
        return "intent"  # clé conservée pour compatibilité → mappe vers "deploy"

    if state.get("needs_security_regen"):
        violations = state.get("security_violations") or []
        logger.warning(
            "[Router] Security-only Checkov failures (%d violation(s)) → security_regen",
            len(violations),
        )
        return "security_regen"

    modules = state.get("modules_to_fix") or []
    if modules:
        logger.info("[Router] Fixable modules: %s → fix", modules)
        return "fix"

    artifacts = state.get("artifacts") or {}
    gen_error = artifacts.get("generation_error")
    if gen_error:
        logger.warning("[Router] generation_error='%s', no modules to fix → escalate", gen_error)
    else:
        logger.warning("[Router] Validation failed, no fixable modules → escalate")
    return "escalate"


# ─────────────────────────────────────────────────────────────────────────────
# route_runner_result
# ─────────────────────────────────────────────────────────────────────────────

def route_runner_result(state: MigrationState) -> str:
    """Route après wait_runner basé sur la classification d'échec.

    C-2: Tout échec au stage 'apply' escalade immédiatement — un état cloud
         partiel peut exister, la régénération automatique sur des ressources
         live n'est jamais sûre.
    """
    from agents.state_reconciliation import resolve_canonical_state

    job_id = state.get("runner_job_id")
    if job_id:
        canonical = resolve_canonical_state(job_id)
        if canonical.get("conflict_detected"):
            logger.warning(
                "[Router] route_runner_result: split-brain conflict detected for job=%s "
                "(source_of_truth=%s) — escalating to human review",
                job_id, canonical.get("source_of_truth"),
            )
            return "escalate"
        if canonical["canonical_status"] == "done":
            logger.info("[Router] route_runner_result: canonical=done → success")
            return "success"
        if canonical["canonical_status"] == "failed" and canonical["runner_error_class"]:
            state = {**state, "runner_failure_class": canonical["runner_error_class"]}

    cls = state.get("runner_failure_class")
    if not cls:
        return "success"

    history = state.get("runner_failure_history") or []
    last = history[-1] if history else {}

    # C-2: échec au stage apply → TOUJOURS escalader
    if last.get("stage") == "apply":
        logger.warning(
            "[Router] route_runner_result: apply-stage failure (class=%s) → escalate "
            "(partial cloud state may exist — human review required)", cls,
        )
        return "escalate"

    # Circuit breaker absolu
    if (state.get("runner_total_attempts") or 0) >= MAX_RUNNER_TOTAL_ATTEMPTS:
        logger.warning("[Router] route_runner_result: runner_total_attempts ≥ %d → escalate", MAX_RUNNER_TOTAL_ATTEMPTS)
        return "escalate"

    # Kill-switch oscillation : même signature vue ≥ MAX_RUNNER_SIG_REPEATS fois
    sig = state.get("runner_failure_signature")
    if sig and sum(1 for h in history if h.get("sig") == sig) >= MAX_RUNNER_SIG_REPEATS:
        logger.warning(
            "[Router] route_runner_result: failure signature %s repeated ≥ %d times → escalate",
            sig, MAX_RUNNER_SIG_REPEATS,
        )
        return "escalate"

    # Erreurs de codegen (plan-stage uniquement ; apply est géré ci-dessus)
    if cls in ("STATIC", "SEMANTIC", "DEPENDENCY", "POLICY"):
        if (state.get("runner_regen_count") or 0) >= MAX_RUNNER_REGEN_COUNT:
            logger.warning("[Router] route_runner_result: runner_regen_count ≥ %d → escalate", MAX_RUNNER_REGEN_COUNT)
            return "escalate"
        logger.info("[Router] route_runner_result: class=%s → regen", cls)
        return "regen"

    # Erreurs runtime : stage plan = erreur de codegen HCL → regen Agent 02
    #                   stage apply/verify = état cloud partiel possible → retry puis escalade
    if cls == "RUNTIME":
        if last.get("stage") == "plan":
            if (state.get("runner_regen_count") or 0) >= MAX_RUNNER_REGEN_COUNT:
                logger.warning("[Router] route_runner_result: runner_regen_count ≥ %d → escalate", MAX_RUNNER_REGEN_COUNT)
                return "escalate"
            logger.info("[Router] route_runner_result: class=%s stage=plan → regen (HCL fix)", cls)
            return "regen"
        if (state.get("runner_retry_count") or 0) >= MAX_RUNNER_RETRY_COUNT:
            logger.warning("[Router] route_runner_result: runner_retry_count ≥ %d → escalate", MAX_RUNNER_RETRY_COUNT)
            return "escalate"
        logger.info("[Router] route_runner_result: class=%s → retry", cls)
        return "retry"

    # QUOTA / STATE / ENVIRONMENTAL → toujours escalader
    logger.warning("[Router] route_runner_result: class=%s → escalate", cls)
    return "escalate"


# ─────────────────────────────────────────────────────────────────────────────
# route_after_health
# ─────────────────────────────────────────────────────────────────────────────

def route_after_health(state: MigrationState) -> str:
    """Route après health_check vers publish_github ou halt.

    publish_github_node s'exécute TOUJOURS (deploy réussi ou non) car les fichiers
    IaC générés et validés ont de la valeur pour le user même quand terraform apply
    échoue — il peut corriger les problèmes manuellement depuis la PR GitHub.

    Seule exception : conflit split-brain → halt pour éviter un status 'Completed' erroné
    sur une infra en état partiel non réconcilié.

    Logique:
      - conflict_detected → halt   (split-brain, état cloud incertain)
      - tous les autres cas        → publish (IaC disponible, deploy OK ou non)

    Le vrai statut du deploy est dans deployment_status et reflété dans le frontend.
    """
    from agents.state_reconciliation import resolve_canonical_state

    dep_status = state.get("deployment_status", "unknown")

    # Réconciliation contre la DB.
    job_id = state.get("runner_job_id")
    if job_id:
        canonical = resolve_canonical_state(job_id)
        if canonical.get("conflict_detected"):
            logger.warning(
                "[Router] route_after_health: split-brain conflict detected for job=%s "
                "(source_of_truth=%s) — halting to avoid incorrect state",
                job_id, canonical.get("source_of_truth"),
            )
            return "halt"
        if canonical["canonical_status"] == "done":
            dep_status = "deployed"
        elif canonical["canonical_status"] == "failed":
            logger.warning(
                "[Router] route_after_health: canonical=failed (job=%s) — "
                "terraform apply échoué, mais on publie quand même sur GitHub pour que "
                "le user puisse corriger depuis la PR.",
                job_id,
            )
            # dep_status reste "failed" — le frontend l'affiche correctement

    if dep_status in ("blocked", "failed"):
        logger.warning(
            "[Router] route_after_health: deployment_status=%s — "
            "publishing to GitHub anyway so the user can review and fix the IaC.",
            dep_status,
        )

    return "publish"
