"""
deploy_nodes.py - LangGraph nodes for the deployment phase.

Responsibilities:
  enqueue_deploy_node     — copies IaC files and submits a RunnerJob
  wait_runner_node        — checks runner job terminal status on callback resume
  mark_runner_regen_node  — increments the IaC regeneration counter
  mark_runner_retry_node  — increments the retry counter (transient errors)

Internal helpers:
  _timeout_fail           — conditional UPDATE to 'failed' (C-1)
  _runner_sig             — 16-hex failure signature hash
  _make_failure_record    — builds a failure history record
  _build_failure_state    — assembles the state update dict for a failure

Interrupt design (L3):
  The graph is interrupted BEFORE wait_runner (interrupt_before=["wait_runner"]).
  The graph resumes when the external runner-callback endpoint injects the result.
  wait_runner_node then only reads the terminal job status — no polling.

  TODO(L3-step3): Create POST /api/v1/migrations/{id}/runner-callback endpoint.
      Body: {"job_id": str, "status": "success"|"failure", "output": dict}
      The endpoint calls graph.aupdate_state(config, {"runner_job_id": job_id},
      as_node="enqueue_deploy") then graph.ainvoke(None, config) to resume.
  TODO(L3-step4): Update executor/worker.py main() to call the callback URL
      after TerraformRunner.run() completes, so the graph resumes automatically.
"""
import asyncio
import logging
import time
from pathlib import Path

from agents.pipeline_state import MigrationState
from agents.node_decorator import publish_events
from core.constants import MAX_RUNNER_FAILURE_HISTORY
from executor.manifest import DeploymentManifest

logger = logging.getLogger("Graph.Deploy")

import os
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
OUTPUT_DIR: str = _out_env if os.path.isabs(_out_env) else str(
    _PROJECT_ROOT / (_out_env or os.path.join("output", "migrated_app"))
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _timeout_fail(job_id: str, error_msg: str) -> None:
    """C-1: UPDATE conditionnel — ne passe à 'failed' que les jobs non-terminaux.

    Libère le slot de l'index partiel-unique pour permettre un nouvel enqueue.
    Ne peut jamais écraser un 'done' concurrent (garde WHERE).
    """
    from executor.queue import timeout_fail_job
    try:
        timeout_fail_job(job_id, error_msg)
    except Exception as exc:
        logger.warning("[Deploy] _timeout_fail: DB update failed for job %s: %s", job_id, exc)


def _runner_sig(error_class: str, error_detail: dict) -> str:
    from executor.failure_classifier import failure_signature
    return failure_signature(error_class, error_detail)


def _make_failure_record(
    error_class: str,
    error_detail: dict,
    *,
    message_override: str | None = None,
) -> dict:
    return {
        "ts":       time.time(),
        "sig":      _runner_sig(error_class, error_detail),
        "class":    error_class,
        "stage":    error_detail.get("stage"),
        "resource": error_detail.get("resource"),
        "message":  (message_override or error_detail.get("message") or "")[:300],
    }


def _build_failure_state(
    state: MigrationState,
    error_class: str,
    error_detail: dict,
    *,
    message_override: str | None = None,
) -> dict:
    """Assemble le dict de mise à jour d'état pour un résultat d'échec depuis wait_runner."""
    record = _make_failure_record(error_class, error_detail, message_override=message_override)
    history = (list(state.get("runner_failure_history") or []) + [record])[-MAX_RUNNER_FAILURE_HISTORY:]
    return {
        "runner_failure_class":     error_class,
        "runner_failure_signature": record["sig"],
        "runner_failure_history":   history,
        "runner_failure_detail":    error_detail,
        "deployment_status":        "failed",
    }


# ─────────────────────────────────────────────────────────────────────────────
# enqueue_deploy_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="enqueue_deploy")
def enqueue_deploy_node(state: MigrationState) -> dict:
    """Copie les fichiers IaC vers un répertoire par migration et soumet un RunnerJob.

    Exécuté de façon synchrone dans le contexte du nœud LangGraph.
    Aucune exécution Terraform ici — le worker TerraformRunner prend en charge le job.

    Résultats possibles (tous loggés et reflétés dans deployment_status) :
      SKIPPED          — pas de manifest produit (Agent 03 bloqué)
      SKIPPED_BLOCKED  — deployment_status=blocked, enqueue intentionnellement ignoré
      IDEMPOTENT       — job actif existant pour cette migration (garde M-5)
      QUEUED           — nouveau job soumis avec succès dans la queue
      FAILED_COPY      — échec de la copie filesystem du work_dir
      FAILED_ENQUEUE   — échec de l'INSERT DB (contrainte unique ou erreur DB)
    """
    import shutil

    manifest_dict = state.get("deployment_manifest")
    if not manifest_dict:
        logger.warning(
            "[Deploy] enqueue_deploy [SKIPPED]: no deployment_manifest in state "
            "— runner_job_id=None, deployment_status=failed_to_enqueue"
        )
        return {"runner_job_id": None, "deployment_status": "failed_to_enqueue"}

    deployment_status = state.get("deployment_status", "unknown")
    if deployment_status == "blocked":
        logger.info(
            "[Deploy] enqueue_deploy [SKIPPED_BLOCKED]: deployment_status=blocked "
            "— runner_job_id=None (intentional, not a failure)"
        )
        return {"runner_job_id": None}

    if not manifest_dict.get("work_dir"):
        logger.error(
            "[Deploy] enqueue_deploy [FAILED_COPY]: manifest.work_dir is missing "
            "— cannot locate source files, runner_job_id=None"
        )
        return {
            "runner_job_id": None,
            "deployment_manifest": manifest_dict,
            "deployment_status": "failed_to_enqueue",
        }

    migration_id = state.get("migration_id", "")

    # Copie per-migration pour éviter les conflits entre migrations parallèles
    # run_dir MUST be inside the shared volume (/app/output/) so the executor
    # container can access the .tf files via the same Docker volume mount.
    # Use the manifest work_dir (set by _build_manifest from MIGRATION_OUTPUT_DIR)
    # — NOT the module-level OUTPUT_DIR constant which defaults to migrated_app/
    # and causes FileNotFoundError when generator used a per-migration UUID path.
    src_dir = manifest_dict.get("work_dir") or OUTPUT_DIR
    run_dir = str(Path(src_dir).parent / "runs" / migration_id)
    try:
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        # Files that are generated by the terraform runner — never overwrite them.
        _RUNNER_PRESERVED = {
            "terraform.tfstate",
            "terraform.tfstate.backup",
            ".terraform.lock.hcl",
        }
        for item in Path(src_dir).iterdir():
            dst = Path(run_dir) / item.name
            if item.is_dir():
                # Never overwrite .terraform/ plugin cache — it's large and managed by init.
                if item.name == ".terraform" and dst.exists():
                    continue
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                # Always overwrite .tf files so fixers applied after last deployment are applied.
                # Preserve runner-generated state/lock files (terraform.tfstate, .lock.hcl).
                if dst.exists() and item.name in _RUNNER_PRESERVED:
                    continue
                shutil.copy2(item, dst)
        manifest_dict = dict(manifest_dict)
        manifest_dict["work_dir"] = run_dir
        manifest_dict["tf_files"] = [
            str(p.relative_to(run_dir))
            for p in Path(run_dir).glob("*.tf")
        ]
    except Exception as exc:
        logger.error(
            "[Deploy] enqueue_deploy [FAILED_COPY]: filesystem copy failed (%s) "
            "— runner_job_id=None, deployment_status=failed_to_enqueue",
            type(exc).__name__,
        )
        return {
            "runner_job_id": None,
            "deployment_manifest": manifest_dict,
            "deployment_status": "failed_to_enqueue",
        }

    try:
        manifest = DeploymentManifest.model_validate(manifest_dict)
        from executor.queue import enqueue, get_job_for_migration

        existing = get_job_for_migration(migration_id)
        if existing and existing["status"] not in ("done", "failed"):
            logger.info(
                "[Deploy] enqueue_deploy [IDEMPOTENT]: active job already exists "
                "job=%s status=%s migration=%s — skipping duplicate enqueue",
                existing["id"], existing["status"], migration_id,
            )
            return {"runner_job_id": existing["id"], "deployment_manifest": manifest_dict}

        job_id = enqueue(manifest)
        logger.info(
            "[Deploy] enqueue_deploy [QUEUED]: runner_job_id=%s migration=%s "
            "— job submitted to queue, waiting for worker",
            job_id, migration_id,
        )
        return {"runner_job_id": job_id, "deployment_manifest": manifest_dict}
    except Exception as exc:
        logger.error(
            "[Deploy] enqueue_deploy [FAILED_ENQUEUE]: DB insert failed (%s) "
            "— runner_job_id=None, deployment_status=failed_to_enqueue",
            type(exc).__name__,
        )
        return {
            "runner_job_id": None,
            "deployment_manifest": manifest_dict,
            "deployment_status": "failed_to_enqueue",
        }


# ─────────────────────────────────────────────────────────────────────────────
# wait_runner_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="wait_runner")
async def wait_runner_node(state: MigrationState) -> dict:
    """Read terminal runner job status after interrupt-based resume (L3 step 2a).

    The graph is interrupted BEFORE this node (interrupt_before=["wait_runner"]).
    This node executes only after an external caller resumes the graph — at that
    point the job is expected to be in a terminal state ('done' or 'failed').

    If the job is not yet terminal (e.g., resume called too early), the node
    escalates as ENVIRONMENTAL so route_runner_result can decide what to do.

    See module docstring for the TODO items covering step 3 (callback endpoint)
    and step 4 (worker calling the callback).
    """
    from executor.queue import get_job

    job_id = state.get("runner_job_id")

    if not job_id:
        logger.error("[Deploy] wait_runner: runner_job_id missing — escalating as ENVIRONMENTAL")
        return _build_failure_state(
            state, "ENVIRONMENTAL", {},
            message_override="runner_job_id not present in pipeline state",
        )

    try:
        job = await asyncio.to_thread(get_job, job_id)
    except Exception as exc:
        logger.error("[Deploy] wait_runner: get_job failed (%s) — escalating", exc)
        return _build_failure_state(
            state, "ENVIRONMENTAL", {},
            message_override=f"get_job raised: {exc}",
        )

    if job is None:
        logger.error("[Deploy] wait_runner: job %s not found in DB — escalating", job_id)
        return _build_failure_state(
            state, "ENVIRONMENTAL", {},
            message_override=f"runner_job row {job_id} missing from DB",
        )

    current_status = job["status"]
    logger.info("[Deploy] wait_runner: job=%s status=%s at resume", job_id, current_status)

    if current_status == "done":
        return {
            "runner_failure_class":   None,
            "runner_failure_history": list(state.get("runner_failure_history") or []),
            "deployment_status":      "deployed",
            "artifacts": {
                **(state.get("artifacts") or {}),
                "apply_outputs": job.get("apply_outputs") or {},
                "runner_job_id": job_id,
            },
        }

    if current_status == "failed":
        error_class = job.get("error_class") or "RUNTIME"
        error_detail = job.get("error_detail") or {}
        logger.warning(
            "[Deploy] wait_runner: job=%s FAILED class=%s stage=%s",
            job_id, error_class, error_detail.get("stage"),
        )
        return _build_failure_state(state, error_class, error_detail)

    # Not yet terminal — graph was resumed before the runner finished.
    logger.warning(
        "[Deploy] wait_runner: job=%s status=%s — not terminal at resume",
        job_id, current_status,
    )
    return _build_failure_state(
        state, "ENVIRONMENTAL", {},
        message_override=(
            f"wait_runner resumed but job {job_id} is still in status '{current_status}' "
            "— ensure the callback is only called after the runner reaches a terminal state"
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# mark_runner_regen_node / mark_runner_retry_node
# ─────────────────────────────────────────────────────────────────────────────

def mark_runner_regen_node(state: MigrationState) -> dict:
    """Incrémente les compteurs regen + total avant re-entrée dans generate_iac.

    F-1: garde d'idempotence scoped — sentinel propre à ce nœud.
    F-2: retourne les valeurs courantes explicites pour LangGraph (jamais {}).
    """
    job_id = state.get("runner_job_id")
    if job_id and state.get("runner_last_counted_regen_job_id") == job_id:
        logger.info("[Deploy] mark_runner_regen: replay guard hit job=%s — no-op", job_id)
        return {
            "runner_regen_count":    state.get("runner_regen_count") or 0,
            "runner_total_attempts": state.get("runner_total_attempts") or 0,
            "runner_failure_class":  state.get("runner_failure_class"),
        }
    new_regen = (state.get("runner_regen_count") or 0) + 1
    new_total = (state.get("runner_total_attempts") or 0) + 1
    logger.info("[Deploy] mark_runner_regen: regen=%d total=%d", new_regen, new_total)
    return {
        "runner_regen_count":               new_regen,
        "runner_total_attempts":            new_total,
        "runner_failure_class":             None,
        "runner_last_counted_regen_job_id": job_id,
    }


def mark_runner_retry_node(state: MigrationState) -> dict:
    """Incrémente les compteurs retry + total avant re-enqueue du même manifest.

    F-1: garde d'idempotence scoped — indépendant du sentinel regen.
    F-2: retourne les valeurs courantes explicites pour LangGraph (jamais {}).
    """
    job_id = state.get("runner_job_id")
    if job_id and state.get("runner_last_counted_retry_job_id") == job_id:
        logger.info("[Deploy] mark_runner_retry: replay guard hit job=%s — no-op", job_id)
        return {
            "runner_retry_count":    state.get("runner_retry_count") or 0,
            "runner_total_attempts": state.get("runner_total_attempts") or 0,
            "runner_failure_class":  state.get("runner_failure_class"),
        }
    new_retry = (state.get("runner_retry_count") or 0) + 1
    new_total = (state.get("runner_total_attempts") or 0) + 1
    logger.info("[Deploy] mark_runner_retry: retry=%d total=%d", new_retry, new_total)
    return {
        "runner_retry_count":               new_retry,
        "runner_total_attempts":            new_total,
        "runner_failure_class":             None,
        "runner_last_counted_retry_job_id": job_id,
    }
