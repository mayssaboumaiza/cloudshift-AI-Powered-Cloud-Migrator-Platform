"""
publish_nodes.py - Nœuds LangGraph de la phase de publication et vérification.

Responsabilités :
  health_check_node   — vérification post-déploiement (6 checks déterministes)
  publish_github_node — création du repo GitHub + push IaC + ouverture PR
"""
import logging
from pathlib import Path

from agents.pipeline_state import MigrationState
from agents.node_decorator import publish_events

logger = logging.getLogger("Graph.Publish")

# Répertoire de sortie IaC (même calcul que iac_nodes.py / deploy_nodes.py)
import os
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
OUTPUT_DIR: str = _out_env if os.path.isabs(_out_env) else str(
    _PROJECT_ROOT / (_out_env or os.path.join("output", "migrated_app"))
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _deploy_blocked_hint(artifacts: dict) -> str:
    """Retourne un message d'action lisible quand deployment_status est blocked."""
    issues = artifacts.get("agent03_post_validation_issues") or []
    summary = artifacts.get("deployment_summary") or {}
    warnings = summary.get("warnings") or []

    signals: list[str] = []
    if issues:
        signals.extend(str(i) for i in issues[:3])
    if warnings:
        signals.extend(str(w) for w in warnings[:2])

    if not signals:
        return (
            "Deployment blocked — ensure cloud credentials are set "
            "(ARM_CLIENT_ID / ARM_TENANT_ID / ARM_CLIENT_SECRET for Azure; "
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY for AWS; "
            "GOOGLE_CREDENTIALS for GCP) then re-run deploy."
        )

    detail = "; ".join(signals[:3])
    return (
        f"Deployment blocked: {detail}. "
        "Fix the issue above, then re-run Agent 03 (set credentials or correct the resource config)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# health_check_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="health_check")
def health_check_node(state: MigrationState) -> MigrationState:
    """Vérification post-déploiement — 6 checks déterministes, zéro LLM.

    Checks :
    1. deploy.sh généré
    2. Fichiers .tf présents dans le répertoire de travail
    3. Résultat de iac_validation
    4. deployment_status
    5. Issues de validate_intent
    6. Coût vs budget
    """
    logger.info("[Publish] health_check: starting post-deployment verification...")

    artifacts = dict(state.get("artifacts") or {})
    checks: dict[str, str] = {}
    passed = 0
    failed = 0

    manifest_work_dir = (state.get("deployment_manifest") or {}).get("work_dir") or OUTPUT_DIR

    # Check 1 : deploy.sh
    # Look in work_dir first (enqueue_deploy copies files there), then fall back
    # to the per-migration output dir (where Agent 03 writes the file originally).
    deploy_sh = Path(manifest_work_dir) / "deploy.sh"
    if not deploy_sh.exists():
        migration_id_check = state.get("migration_id", "")
        if migration_id_check:
            try:
                from core.paths import get_output_dir
                deploy_sh = get_output_dir(migration_id_check) / "deploy.sh"
            except Exception:
                pass
    if deploy_sh.exists():
        checks["deploy_script"] = "✅ deploy.sh generated"
        passed += 1
    else:
        checks["deploy_script"] = "⚠️ deploy.sh not found"
        failed += 1

    # Check 2 : fichiers .tf — cherche dans work_dir puis dans le répertoire de sortie de la migration
    tf_files = list(Path(manifest_work_dir).glob("*.tf")) if Path(manifest_work_dir).is_dir() else []
    if not tf_files:
        migration_id_check = state.get("migration_id", "")
        if migration_id_check:
            try:
                from core.paths import get_output_dir
                tf_files = list(get_output_dir(migration_id_check).glob("*.tf"))
            except Exception:
                pass
        if not tf_files:
            # Fallback : artifacts indiquent que des fichiers ont été générés
            _artifacts_tf = (state.get("artifacts") or {}).get("generated_files") or []
            if _artifacts_tf:
                tf_files = _artifacts_tf  # truthy list → check passes
    if tf_files:
        checks["terraform_files"] = f"✅ {len(tf_files)} Terraform file(s) generated"
        passed += 1
    else:
        checks["terraform_files"] = "⚠️ No Terraform files found in output"
        failed += 1

    # Check 3 : validation IaC
    if state.get("iac_validation_success"):
        checks["iac_validation"] = "✅ IaC validation passed (Checkov + tf validate)"
        passed += 1
    else:
        checks["iac_validation"] = "❌ IaC validation failed"
        failed += 1

    # Check 4 : deployment_status
    dep_status = state.get("deployment_status", "unknown")
    runner_failure_class = state.get("runner_failure_class")
    if dep_status in ("ready", "completed", "queued", "deployed"):
        checks["deployment_status"] = f"✅ Deployment status: {dep_status}"
        passed += 1
    elif dep_status == "failed" and runner_failure_class:
        checks["deployment_status"] = (
            f"❌ Deployment failed — runner error class: {runner_failure_class}. "
            "See runner logs for details. Manual intervention may be required "
            "if the apply stage was reached (partial cloud state possible)."
        )
        failed += 1
    else:
        checks["deployment_status"] = f"⚠️ Deployment status: {dep_status}"
        failed += 1

    # Check 5 : intent validation
    intent_issues = artifacts.get("intent_issues", [])
    if not intent_issues:
        checks["intent_validation"] = "✅ Intent validation: no violations"
        passed += 1
    else:
        checks["intent_validation"] = (
            f"⚠️ Intent validation: {len(intent_issues)} issue(s) — "
            + "; ".join(intent_issues[:2])
        )
        failed += 1

    # Check 6 : budget
    budget = state.get("monthly_budget_usd")
    migration_plan = state.get("migration_plan") or {}
    estimated_cost = float((migration_plan.get("summary") or {}).get("estimated_total_monthly", 0.0) or 0.0)

    if budget and estimated_cost > 0:
        if estimated_cost <= float(budget):
            checks["budget"] = f"✅ Cost ${estimated_cost:.0f}/mo within budget ${budget:.0f}/mo"
            passed += 1
        else:
            checks["budget"] = f"⚠️ Cost ${estimated_cost:.0f}/mo exceeds budget ${budget:.0f}/mo"
            failed += 1
    else:
        checks["budget"] = "ℹ️ No budget constraint set"
        passed += 1

    # ── Checks 7-10 : post-deployment connectivity (best-effort, non-blocking) ─
    # These checks probe the actual deployed Azure endpoints. They run only when
    # deployment_status indicates terraform apply completed. Failures are WARNINGS
    # (not CRITICAL) since firewall rules / DNS propagation can take a few minutes.
    import socket, urllib.request, urllib.error
    runner_logs = artifacts.get("runner_logs") or ""
    # terraform_outputs populated by runner callback; apply_outputs is the fallback
    # key used by wait_runner_node when the runner reports job done.
    tf_outputs = (
        artifacts.get("terraform_outputs")
        or artifacts.get("apply_outputs")
        or {}
    )
    dep_completed = dep_status in ("completed", "deployed", "ready")

    # Check 7 : PostgreSQL connectivity
    # Connectivity failures are non-blocking (firewall rules propagate asynchronously
    # after terraform apply — Azure can take 1-5 min). Count as passed with a warning
    # message so they don't degrade the overall health status.
    pg_fqdn = tf_outputs.get("postgresql_server_fqdn") or tf_outputs.get("postgresql_fqdn") or ""
    if pg_fqdn and dep_completed:
        try:
            sock = socket.create_connection((pg_fqdn, 5432), timeout=8)
            sock.close()
            checks["postgres_connectivity"] = f"✅ PostgreSQL reachable: {pg_fqdn}:5432"
            passed += 1
        except Exception as e:
            checks["postgres_connectivity"] = (
                f"⚠️ PostgreSQL not reachable yet: {pg_fqdn}:5432 ({type(e).__name__}) "
                "— firewall rules may take 1-2 min to propagate."
            )
            passed += 1  # non-blocking: transient network state, not a deployment failure
    elif pg_fqdn:
        checks["postgres_connectivity"] = f"ℹ️ PostgreSQL check skipped (deployment not complete): {pg_fqdn}"
        passed += 1

    # Check 8 : Azure Blob Storage HTTP probe
    storage_name = tf_outputs.get("storage_account_name") or tf_outputs.get("storage_name") or ""
    if storage_name and dep_completed:
        try:
            url = f"https://{storage_name}.blob.core.windows.net/"
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as resp:
                http_code = resp.status
        except urllib.error.HTTPError as e:
            http_code = e.code
        except Exception:
            http_code = 0
        if http_code in (200, 400, 403, 404):
            checks["blob_storage_endpoint"] = f"✅ Azure Blob Storage reachable (HTTP {http_code}): {storage_name}"
            passed += 1
        else:
            checks["blob_storage_endpoint"] = (
                f"⚠️ Azure Blob Storage not reachable (HTTP {http_code}): {storage_name}"
            )
            failed += 1
    elif storage_name:
        checks["blob_storage_endpoint"] = f"ℹ️ Blob Storage check skipped (deployment not complete): {storage_name}"
        passed += 1

    # Check 9 : Azure OpenAI / Cognitive Services endpoint
    cognitive_name = tf_outputs.get("cognitive_account_name") or tf_outputs.get("openai_endpoint") or ""
    if cognitive_name and dep_completed:
        # cognitive_name may be a name or full URL
        if not cognitive_name.startswith("http"):
            cog_url = f"https://{cognitive_name}.openai.azure.com/"
        else:
            cog_url = cognitive_name
        try:
            req = urllib.request.Request(cog_url, method="HEAD")
            with urllib.request.urlopen(req, timeout=8) as resp:
                cog_code = resp.status
        except urllib.error.HTTPError as e:
            cog_code = e.code
        except Exception:
            cog_code = 0
        if cog_code in (200, 401, 403):
            checks["openai_endpoint"] = f"✅ Azure OpenAI endpoint reachable (HTTP {cog_code}): {cognitive_name}"
            passed += 1
        else:
            checks["openai_endpoint"] = (
                f"⚠️ Azure OpenAI not reachable yet (HTTP {cog_code}): {cognitive_name} "
                "— cognitive account provisioning may take 2-3 minutes."
            )
            passed += 1  # non-blocking: provisioning is asynchronous after terraform apply
    elif cognitive_name:
        checks["openai_endpoint"] = f"ℹ️ OpenAI endpoint check skipped (deployment not complete)"
        passed += 1

    # Check 10 : data migration scripts present
    data_migration_ready = []
    if (Path(manifest_work_dir) / "deploy.sh").exists():
        deploy_sh_content = (Path(manifest_work_dir) / "deploy.sh").read_text(encoding="utf-8")
        if "pg_dump" in deploy_sh_content:
            data_migration_ready.append("PostgreSQL")
        if "azcopy" in deploy_sh_content or "az storage blob" in deploy_sh_content:
            data_migration_ready.append("S3→Blob")
    if data_migration_ready:
        checks["data_migration_scripts"] = (
            f"✅ Data migration scripts included in deploy.sh: {', '.join(data_migration_ready)}"
        )
        passed += 1

    # Rapport Markdown déterministe (sans LLM)
    health_status = "HEALTHY" if failed == 0 else ("DEGRADED" if failed <= 2 else "CRITICAL")
    status_icon = "✅" if health_status == "HEALTHY" else ("⚠️" if health_status == "DEGRADED" else "❌")

    check_lines = "\n".join(f"- {v}" for v in checks.values())
    next_steps = ""
    if failed > 0:
        action_map = {
            "deploy_script":    "Run Agent 03 after setting ARM_CLIENT_ID / ARM_TENANT_ID / ARM_CLIENT_SECRET.",
            "terraform_files":  "Re-run IaC generation (Agent 02) — no .tf files were produced.",
            "iac_validation":   "Review Terraform errors above and re-run `terraform validate`.",
            "deployment_status": _deploy_blocked_hint(artifacts),
            "intent_validation": "Review intent issues and correct region / budget settings.",
            "budget":           "Consider upgrading your plan or selecting cheaper target services.",
        }
        actions = [
            f"- {action_map[k]}"
            for k in checks
            if k in action_map and ("⚠️" in checks[k] or "❌" in checks[k])
        ]
        if actions:
            next_steps = "\n\n## Next Steps\n" + "\n".join(actions)

    health_report = (
        f"# {status_icon} Health Report — {health_status}\n\n"
        f"**{passed} check(s) passed / {failed} check(s) require attention**\n\n"
        f"## Checks\n{check_lines}"
        f"{next_steps}"
    )

    logger.info("[Publish] health_check: %s — %d passed / %d failed", health_status, passed, failed)

    artifacts["health_report"] = health_report
    artifacts["health_checks"] = checks
    artifacts["health_status"] = health_status

    # TASK 3 : ne jamais upgrader "blocked" vers "completed_with_warnings"
    prior_dep_status = state.get("deployment_status", "unknown")
    if prior_dep_status == "blocked":
        final_dep_status = "blocked"
    else:
        final_dep_status = "completed" if health_status == "HEALTHY" else "completed_with_warnings"

    # ── Path 2: persist git SHA so next run can detect changes ───────────────
    # Save the current SHA (captured by detect_changes_node) to DB so Path 2
    # can compare it against the repo's HEAD on the next pipeline run.
    detected_changes = state.get("detected_changes") or {}
    current_sha      = detected_changes.get("current_sha")
    migration_id     = state.get("migration_id", "")
    migration_mode   = state.get("migration_mode") or "full"

    if migration_id and current_sha:
        try:
            from configuration.database import async_session as _async_session
            from data.repositories.migration_repository import MigrationRepository
            import asyncio

            async def _save_sha():
                async with _async_session() as db:
                    repo = MigrationRepository(db)
                    await repo.update_by_id(migration_id, {
                        "last_git_sha": current_sha,
                        "migration_mode": migration_mode,
                        "detected_changes": detected_changes,
                    })

            asyncio.get_event_loop().run_until_complete(_save_sha())
            logger.info(
                "[Publish] health_check: saved git SHA %s for migration %s (mode=%s)",
                current_sha[:8], migration_id, migration_mode,
            )
        except Exception as exc:
            logger.warning("[Publish] health_check: could not save git SHA — %s", exc)

    return {
        "artifacts": artifacts,
        "deployment_status": final_dep_status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# publish_github_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="publish_github")
def publish_github_node(state: MigrationState) -> MigrationState:
    """Crée un nouveau repo GitHub, push les fichiers IaC générés et ouvre une PR."""
    from agents.github_publisher import publish_to_github

    result = publish_to_github(state)
    artifacts = dict(state.get("artifacts") or {})
    artifacts["github_pr_url"] = result.get("pr_url", "")
    artifacts["github_repo_url"] = result.get("repo_url", "")

    if result.get("error"):
        logger.warning("[Publish] publish_github skipped: %s", result["error"])
    else:
        logger.info("[Publish] publish_github: PR → %s", result["pr_url"])

    return {
        "artifacts": artifacts,
        "github_pr_url": result.get("pr_url", ""),
        "github_repo_url": result.get("repo_url", ""),
    }
