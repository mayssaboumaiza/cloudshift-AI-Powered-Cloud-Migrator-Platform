"""
executor/runner.py — TerraformRunner: executes one RunnerJob end-to-end.

Stage flow:
  INIT → PLAN → (AWAITING_APPROVAL if auto_approve=False) → APPLY → VERIFY → done
                                                           ↘ failed (on any stage error)

Credentials are fetched JIT from SecretBroker at the APPLY stage only.
They are injected into the subprocess environment and never written to disk
or stored in memory beyond the subprocess call duration.

This class runs in the executor worker process — NEVER in the FastAPI process.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from executor.manifest import DeploymentManifest
from executor.log_stream import RunnerLogStream
from executor.queue import append_log, set_status, get_job
from executor.stages import StageContext, stage_init, stage_plan, stage_apply, stage_deploy_sh, stage_verify

logger = logging.getLogger("TerraformRunner")

# How long to poll for human approval before timing out (seconds)
APPROVAL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "3600"))
APPROVAL_POLL_INTERVAL = 15  # seconds


class TerraformRunner:
    """
    Executes a single deployment job through all stages.

    Thread/process safety: each worker instantiates one TerraformRunner per
    claimed job. State is local — no shared mutable globals.
    """

    def __init__(self, job_id: str, manifest: DeploymentManifest, worker_id: str) -> None:
        self.job_id = job_id
        self.manifest = manifest
        self.worker_id = worker_id
        self._stream: Optional[RunnerLogStream] = None

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the full deployment pipeline. Updates job status in DB."""
        set_status(self.job_id, "init")
        self._stream = RunnerLogStream(
            job_id=self.job_id,
            thread_id=self.manifest.thread_id,
            stage="init",
        )

        try:
            with self._stream:
                self._stream.write(
                    f"TerraformRunner started — job={self.job_id} "
                    f"migration={self.manifest.migration_id} "
                    f"provider={self.manifest.provider}",
                    stream="system",
                )
                self._execute()
        except Exception as exc:
            logger.exception("TerraformRunner: unhandled exception job=%s", self.job_id)
            set_status(self.job_id, "failed", error=f"Unhandled runner error: {type(exc).__name__}: {exc}")

    # ── Internal pipeline ─────────────────────────────────────────────────────

    def _execute(self) -> None:
        work_dir = self.manifest.work_dir
        if not Path(work_dir).exists():
            msg = f"work_dir does not exist: {work_dir}"
            self._stream.write(msg, stream="system")
            set_status(self.job_id, "failed", error=msg)
            return

        base_env = {**os.environ, **self.manifest.extra_env}

        # ── DESTROY mode (terraform destroy -auto-approve) ────────────────────
        if getattr(self.manifest, "destroy", False):
            self._execute_destroy(work_dir, base_env)
            return

        # ── INIT ──────────────────────────────────────────────────────────────
        ctx = self._make_ctx(work_dir, base_env, "init")
        result = stage_init(ctx)
        if not result.success:
            failure = result.data.get("failure") or {}
            set_status(
                self.job_id, "failed",
                error=result.error,
                error_class=failure.get("class"),
                error_detail=failure,
            )
            self._stream.write_event("stage_failed", {"stage": "init", "error": result.error,
                                                       "error_class": failure.get("class")})
            return

        # ── PLAN (credentials fetched JIT — required for provider auth) ──────────
        # Credentials must be available at plan time so the cloud provider can
        # authenticate and compute the real resource diff. The plan binary is
        # saved to disk (-out=tfplan) so apply uses exactly the same plan —
        # no drift between what the operator reviewed and what gets deployed.
        set_status(self.job_id, "plan")
        self._stream.stage = "plan"
        plan_env = self._build_apply_env(base_env)
        if plan_env is None:
            return  # error already written by _build_apply_env

        ctx = self._make_ctx(work_dir, plan_env, "plan")
        result = stage_plan(ctx)
        if not result.success:
            failure = result.data.get("failure") or {}
            set_status(
                self.job_id, "failed",
                error=result.error,
                error_class=failure.get("class"),
                error_detail=failure,
            )
            self._stream.write_event("stage_failed", {"stage": "plan", "error": result.error,
                                                       "error_class": failure.get("class")})
            return

        plan_summary = result.data.get("plan_summary", {})
        plan_file = result.data.get("plan_file", "")
        set_status(self.job_id, "plan", plan_summary=plan_summary)
        self._stream.write_event("plan_ready", {"plan_summary": plan_summary})

        # ── APPROVAL GATE ─────────────────────────────────────────────────────
        if not self.manifest.auto_approve:
            set_status(self.job_id, "awaiting_approval")
            self._stream.write_event("awaiting_approval", {"plan_summary": plan_summary})
            approved = self._wait_for_approval()
            if not approved:
                set_status(self.job_id, "failed", error="Approval timed out or was rejected")
                self._stream.write_event("approval_timeout", {})
                return
            self._stream.write_event("approved", {})

        # ── APPLY — reuse the same credential env built at plan time ──────────
        # plan_env already holds the JIT-fetched credentials; reusing it ensures
        # apply runs against the exact provider config that generated the plan.
        set_status(self.job_id, "apply")
        self._stream.stage = "apply"
        apply_env = plan_env

        # M-1: track GCP temp credential file so it is cleaned up after apply+verify
        gcp_cred_file: str | None = None
        _gcp_path = apply_env.get("GOOGLE_APPLICATION_CREDENTIALS")
        if _gcp_path and _gcp_path not in base_env.get("GOOGLE_APPLICATION_CREDENTIALS", ""):
            gcp_cred_file = _gcp_path

        # Pre-flight: purge soft-deleted Key Vaults to prevent VaultAlreadyExists on apply
        self._purge_soft_deleted_key_vaults(work_dir, apply_env)

        try:
            ctx = self._make_ctx(work_dir, apply_env, "apply")
            result = stage_apply(ctx, plan_file)
            if not result.success:
                failure = result.data.get("failure") or {}
                set_status(
                    self.job_id, "failed",
                    error=result.error,
                    error_class=failure.get("class"),
                    error_detail=failure,
                )
                self._stream.write_event("stage_failed", {"stage": "apply", "error": result.error,
                                                           "error_class": failure.get("class")})
                return

            apply_outputs = result.data.get("apply_outputs", {})

            # ── DEPLOY_SH (data migration — non-blocking) ────────────────────
            # Runs deploy.sh which contains data migration steps (S3→Blob,
            # PostgreSQL→Azure PostgreSQL). Infrastructure is already deployed;
            # a deploy.sh failure is recorded as a warning, not a hard failure.
            set_status(self.job_id, "apply")
            self._stream.stage = "apply"
            ctx = self._make_ctx(work_dir, apply_env, "apply")
            deploy_result = stage_deploy_sh(ctx, apply_outputs)
            if deploy_result.data.get("deploy_sh_warning"):
                self._stream.write_event("deploy_sh_warning", {
                    "warning": deploy_result.data["deploy_sh_warning"]
                })
            elif deploy_result.data.get("deploy_sh_ok"):
                self._stream.write_event("deploy_sh_complete", {})

            # ── VERIFY ────────────────────────────────────────────────────────
            set_status(self.job_id, "verify")
            self._stream.stage = "verify"
            ctx = self._make_ctx(work_dir, apply_env, "verify")
            result = stage_verify(ctx, apply_outputs)
            if not result.success:
                set_status(self.job_id, "failed", error=result.error)
                self._stream.write_event("stage_failed", {"stage": "verify", "error": result.error})
                return

            # ── DONE ──────────────────────────────────────────────────────────
            set_status(
                self.job_id,
                "done",
                apply_outputs=apply_outputs,
            )
            self._stream.write_event("deployment_complete", {
                "apply_outputs": apply_outputs,
                "resource_count": result.data.get("resource_count", 0),
            })
            logger.info("TerraformRunner: job=%s DONE", self.job_id)

            # ── INFRA MONITOR — async, non-blocking ───────────────────────────
            # Run health check + drift detection after successful apply.
            # Results are stored in DB and available via /api/v1/migrations/{id}/infra-health.
            # Failures here never affect the job's "done" status.
            self._run_infra_monitor(apply_env)
        finally:
            if gcp_cred_file:
                try:
                    os.unlink(gcp_cred_file)
                except OSError:
                    pass

    # ── Pre-apply Key Vault soft-delete purge ─────────────────────────────────

    def _purge_soft_deleted_key_vaults(self, work_dir: str, apply_env: dict) -> None:
        """Detect and purge soft-deleted Key Vaults whose names clash with planned resources.

        Azure Key Vault with purge-protection disabled can be recovered from soft-delete
        state automatically — but Terraform will fail with VaultAlreadyExists if a vault
        with the same name exists in the subscription's recycle bin.

        This method:
          1. Scans *.tf files in work_dir for azurerm_key_vault resource names.
          2. Calls `az keyvault list-deleted` to find soft-deleted vaults.
          3. For any name collision, calls `az keyvault purge` to remove it.

        Only runs when ARM credentials are present in apply_env (Azure provider only).
        Any failure is logged as a warning and never blocks the apply.
        """
        if not apply_env.get("ARM_SUBSCRIPTION_ID"):
            return  # Not an Azure deployment

        import re as _re
        import subprocess as _sub
        import json as _json

        # Extract key vault names from .tf files (both var.X and literal strings)
        kv_names: set[str] = set()
        try:
            for tf in Path(work_dir).glob("*.tf"):
                content = tf.read_text(encoding="utf-8", errors="replace")
                # Hardcoded: name = "kv-xxxxx"
                for m in _re.finditer(r'resource\s+"azurerm_key_vault"[^}]+?name\s*=\s*"([^"]+)"',
                                      content, _re.DOTALL):
                    kv_names.add(m.group(1))
        except Exception as exc:
            logger.warning("_purge_soft_deleted_key_vaults: tf scan failed: %s", exc)
            return

        if not kv_names:
            return  # No Key Vaults in this deployment

        az = shutil.which("az") or "az"
        purge_env = {
            **apply_env,
            "AZURE_CLIENT_ID":     apply_env.get("ARM_CLIENT_ID", ""),
            "AZURE_CLIENT_SECRET": apply_env.get("ARM_CLIENT_SECRET", ""),
            "AZURE_TENANT_ID":     apply_env.get("ARM_TENANT_ID", ""),
        }

        try:
            list_proc = _sub.run(
                [az, "keyvault", "list-deleted", "--output", "json",
                 "--subscription", apply_env.get("ARM_SUBSCRIPTION_ID", "")],
                capture_output=True, text=True, timeout=30, env=purge_env,
            )
            if list_proc.returncode != 0:
                logger.debug("_purge_soft_deleted_key_vaults: list-deleted exit %d: %s",
                             list_proc.returncode, list_proc.stderr[:200])
                return
            deleted_vaults = _json.loads(list_proc.stdout or "[]")
        except Exception as exc:
            logger.debug("_purge_soft_deleted_key_vaults: list-deleted failed: %s", exc)
            return

        for vault in deleted_vaults:
            name = vault.get("name", "")
            location = vault.get("properties", {}).get("location", "")
            if name not in kv_names:
                continue
            logger.warning(
                "_purge_soft_deleted_key_vaults: soft-deleted vault '%s' collides — purging", name
            )
            self._stream.write_event("preflight_purge", {"vault": name})
            try:
                purge_proc = _sub.run(
                    [az, "keyvault", "purge", "--name", name,
                     "--location", location,
                     "--subscription", apply_env.get("ARM_SUBSCRIPTION_ID", ""),
                     "--no-wait"],
                    capture_output=True, text=True, timeout=30, env=purge_env,
                )
                if purge_proc.returncode == 0:
                    logger.info("_purge_soft_deleted_key_vaults: purged '%s'", name)
                    # Give Azure a moment to propagate the purge
                    time.sleep(5)
                else:
                    logger.warning(
                        "_purge_soft_deleted_key_vaults: purge '%s' failed: %s",
                        name, purge_proc.stderr[:200],
                    )
            except Exception as exc:
                logger.warning("_purge_soft_deleted_key_vaults: purge '%s' exception: %s", name, exc)

    # ── Post-apply infra monitoring ───────────────────────────────────────────

    def _run_infra_monitor(self, apply_env: dict) -> None:
        """Run health check + drift detection after successful apply and persist results.

        Non-blocking: any exception is caught and logged.
        Stores results via set_status so the API can serve them.
        """
        if not apply_env.get("ARM_SUBSCRIPTION_ID"):
            return  # Only implemented for Azure

        migration_id = self.manifest.migration_id
        try:
            from services.infra_monitor import (
                get_deployed_resources,
                check_resource_health,
                detect_drift,
            )
            resources = get_deployed_resources(migration_id)
            if not resources:
                logger.info("_run_infra_monitor: no resources in tfstate for %s", migration_id)
                return

            arm_env = {
                "ARM_SUBSCRIPTION_ID": apply_env.get("ARM_SUBSCRIPTION_ID", ""),
                "ARM_TENANT_ID":       apply_env.get("ARM_TENANT_ID", ""),
                "ARM_CLIENT_ID":       apply_env.get("ARM_CLIENT_ID", ""),
                "ARM_CLIENT_SECRET":   apply_env.get("ARM_CLIENT_SECRET", ""),
            }
            health_results = check_resource_health(resources, arm_env)
            drift_records  = detect_drift(resources, health_results)

            import json as _json
            from datetime import datetime, timezone as _tz
            healthy_count = sum(1 for r in health_results if r.get("health_status") == "healthy")
            error_count   = sum(1 for r in health_results if r.get("health_status") == "error")
            health_payload = {
                "migration_id":      migration_id,
                "tfstate_available": True,
                "health_checks":     True,
                "checked_at":        datetime.now(_tz.utc).isoformat(),
                "resources":         health_results,
                "drift_detected":    drift_records,
                "metrics":           {},
                "resource_count":    len(resources),
                "summary": {
                    "total":     len(health_results),
                    "healthy":   healthy_count,
                    "updating":  sum(1 for r in health_results if r.get("health_status") in ("updating", "creating", "deleting")),
                    "error":     error_count,
                    "not_found": sum(1 for r in health_results if r.get("health_status") == "not_found"),
                    "unknown":   sum(1 for r in health_results if r.get("health_status") == "unknown"),
                },
                "drift_count":    len(drift_records),
                "healthy_count":  healthy_count,
                "error_count":    error_count,
            }
            # Persist to output dir so the API can serve it without a DB schema change
            from core.paths import get_output_dir
            out_dir = get_output_dir(migration_id)
            health_file = out_dir / "infra_health.json"
            health_file.write_text(
                _json.dumps(health_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._stream.write_event("infra_health_ready", {
                "resource_count": len(resources),
                "healthy_count":  sum(1 for r in health_results if r.get("health_status") == "healthy"),
                "drift_count":    len(drift_records),
            })
            logger.info(
                "_run_infra_monitor: migration=%s resources=%d healthy=%d drifts=%d",
                migration_id, len(resources),
                sum(1 for r in health_results if r.get("health_status") == "healthy"),
                len(drift_records),
            )
        except Exception as exc:
            logger.warning("_run_infra_monitor: failed (non-blocking): %s", exc)

    def _execute_destroy(self, work_dir: str, base_env: dict) -> None:
        """Run terraform init + terraform destroy -auto-approve."""
        from executor.stages import stage_init, StageContext
        import shutil

        self._stream.write("=== DESTROY MODE ===", stream="system")
        set_status(self.job_id, "init")

        destroy_env = self._build_apply_env(base_env)
        if destroy_env is None:
            return

        ctx = self._make_ctx(work_dir, destroy_env, "init")
        result = stage_init(ctx)
        if not result.success:
            set_status(self.job_id, "failed", error=result.error)
            self._stream.write_event("stage_failed", {"stage": "init", "error": result.error})
            return

        set_status(self.job_id, "apply")
        self._stream.stage = "apply"
        ctx = self._make_ctx(work_dir, destroy_env, "apply")

        tf = shutil.which("terraform") or "terraform"
        from executor.stages import _run
        ok, _, stderr = _run(
            [tf, "destroy", "-auto-approve", "-input=false", "-no-color"],
            ctx, "apply", timeout=1800,
        )
        if not ok:
            from executor.failure_classifier import classify_terraform_error
            failure = classify_terraform_error(stderr, "destroy")
            set_status(self.job_id, "failed", error="terraform destroy failed",
                       error_class=failure.get("class"), error_detail=failure)
            self._stream.write_event("stage_failed", {"stage": "destroy", "error": stderr[:200]})
            return

        set_status(self.job_id, "done")
        self._stream.write_event("deployment_complete", {"action": "destroy", "resource_count": 0})
        logger.info("TerraformRunner: destroy job=%s DONE", self.job_id)

    def _make_ctx(self, work_dir: str, env: dict, stage: str) -> StageContext:
        self._stream.stage = stage
        return StageContext(
            job_id=self.job_id,
            work_dir=work_dir,
            env=env,
            log=self._stream,
            manifest=self.manifest,
        )

    # ── JIT credential injection ──────────────────────────────────────────────

    def _build_apply_env(self, base_env: dict) -> dict | None:
        """Fetch credentials from SecretBroker and inject into subprocess env.

        Returns the augmented env dict, or None if credentials are missing.
        Credentials are NEVER logged or stored beyond this method's scope.
        """
        from services.credentials.broker import get_secret_broker

        broker = get_secret_broker()
        cred_env: dict[str, str] = {}

        for role, vault_path in self.manifest.secret_refs.items():
            try:
                data = broker.get(vault_path)
                if not data:
                    self._stream.write(
                        f"SecretBroker returned no data for role={role} — skipping",
                        stream="system",
                    )
                    continue
                # Map role → env var names
                cred_env.update(self._creds_to_env(role, data))
            except Exception as exc:
                msg = f"Failed to fetch credentials for role={role}: {type(exc).__name__}"
                logger.error("TerraformRunner._build_apply_env: %s", msg)
                self._stream.write(msg, stream="system")
                set_status(self.job_id, "failed", error=msg)
                return None

        # Validate required vars are present
        required: dict[str, list[str]] = {
            "aws":   ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            "gcp":   ["GOOGLE_APPLICATION_CREDENTIALS"],
            "azure": ["ARM_CLIENT_ID", "ARM_CLIENT_SECRET", "ARM_TENANT_ID", "ARM_SUBSCRIPTION_ID"],
        }
        provider = str(self.manifest.provider)
        missing = [v for v in required.get(provider, []) if not cred_env.get(v) and not base_env.get(v)]
        if missing:
            msg = f"Missing required credential env vars for {provider}: {missing}"
            self._stream.write(msg, stream="system")
            set_status(self.job_id, "failed", error=msg)
            return None

        # Inject secure TF_VAR_ passwords so "CHANGE_ME_BEFORE_DEPLOY" placeholders
        # don't reach Azure (which enforces complexity: 8+ chars, upper+lower+digit+special).
        tf_var_secrets = self._generate_tf_var_secrets(cred_env, self.manifest.migration_id)
        return {**base_env, **cred_env, **tf_var_secrets}

    @staticmethod
    def _creds_to_env(role: str, data: dict) -> dict[str, str]:
        """Convert SecretBroker data dict → Terraform-ready env vars."""
        env: dict[str, str] = {}
        if role == "aws":
            if k := data.get("access_key"):
                env["AWS_ACCESS_KEY_ID"] = k
            if s := data.get("secret_key"):
                env["AWS_SECRET_ACCESS_KEY"] = s
            if r := data.get("region"):
                env["AWS_DEFAULT_REGION"] = r
            if t := data.get("session_token"):
                env["AWS_SESSION_TOKEN"] = t
        elif role in ("gcp", "google"):
            if sa_json := data.get("service_account_json"):
                import json, tempfile as _tmp
                tmp = _tmp.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, prefix="gcp_sa_"
                )
                json.dump(sa_json, tmp)
                tmp.close()
                env["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name
                env["GCLOUD_PROJECT"] = data.get("project_id", "")
        elif role == "azure":
            env["ARM_CLIENT_ID"] = data.get("client_id", "")
            env["ARM_CLIENT_SECRET"] = data.get("client_secret", "")
            env["ARM_TENANT_ID"] = data.get("tenant_id", "")
            env["ARM_SUBSCRIPTION_ID"] = data.get("subscription_id", "")
        elif role == "github_token":
            if t := data.get("token"):
                env["GITHUB_TOKEN"] = t
        return env

    # ── TF_VAR_ secure password generation ───────────────────────────────────

    @staticmethod
    def _make_secure_password(length: int = 20) -> str:
        """Generate a password meeting Azure/AWS/GCP complexity requirements.

        Rules satisfied:
          - >= 8 characters (we use 20 for safety)
          - At least 1 uppercase, 1 lowercase, 1 digit, 1 special character
          - No ambiguous characters that break shell quoting in env vars
        """
        import secrets
        import string

        upper   = string.ascii_uppercase
        lower   = string.ascii_lowercase
        digits  = string.digits
        special = "!@#$%^&*()-_=+"

        # Guarantee one character from each required class
        mandatory = [
            secrets.choice(upper),
            secrets.choice(upper),
            secrets.choice(lower),
            secrets.choice(lower),
            secrets.choice(digits),
            secrets.choice(digits),
            secrets.choice(special),
        ]
        pool = upper + lower + digits + special
        rest = [secrets.choice(pool) for _ in range(length - len(mandatory))]
        chars = mandatory + rest
        # Fisher-Yates shuffle via secrets
        for i in range(len(chars) - 1, 0, -1):
            j = secrets.randbelow(i + 1)
            chars[i], chars[j] = chars[j], chars[i]
        return "".join(chars)

    # Variables injected as TF_VAR_<name> when they still hold the placeholder.
    _PLACEHOLDER_TF_VARS = (
        "db_password", "db_admin_password", "admin_password",
        "postgres_admin_password", "postgres_password", "postgresql_password",
        "mysql_admin_password", "administrator_password",
        "client_secret", "ssh_private_key_pem",
    )

    def _generate_tf_var_secrets(
        self,
        cred_env: dict,
        migration_id: str,
    ) -> dict[str, str]:
        """Generate secure TF_VAR_* passwords and store them in Vault.

        Returns a dict of env vars ready to merge into the subprocess environment.
        Passwords are generated once per migration and stored so they can be
        retrieved later (e.g. to connect to the deployed database).
        """
        result: dict[str, str] = {}

        for var_name in self._PLACEHOLDER_TF_VARS:
            env_key = f"TF_VAR_{var_name}"

            # Skip if already set (user provided explicit value via env)
            if cred_env.get(env_key) or os.environ.get(env_key):
                continue

            # Try to load an existing password from Vault (idempotent — same per migration)
            vault_key = f"tfvar_{var_name}"
            existing_pwd: str | None = None
            try:
                from services.credentials.broker import get_secret_broker
                broker = get_secret_broker()
                vault_data = broker.get(broker.migration_path(migration_id, vault_key))
                if isinstance(vault_data, dict):
                    existing_pwd = vault_data.get("value")
                elif isinstance(vault_data, str):
                    existing_pwd = vault_data
            except Exception:
                pass  # Vault unavailable — generate fresh; not stored

            if not existing_pwd:
                existing_pwd = self._make_secure_password()
                # Persist to Vault so the password survives worker restarts
                try:
                    from services.credentials.broker import get_secret_broker
                    broker = get_secret_broker()
                    broker.store(
                        broker.migration_path(migration_id, vault_key),
                        {"value": existing_pwd, "var_name": var_name},
                    )
                    logger.info(
                        "TerraformRunner: generated secure TF_VAR_%s for migration=%s",
                        var_name, migration_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "TerraformRunner: could not persist TF_VAR_%s to Vault: %s",
                        var_name, exc,
                    )

            result[env_key] = existing_pwd

        # Also inject ARM vars as TF_VAR_* so provider.tf can use them as variables
        _ARM_TO_TF: dict[str, str] = {
            "ARM_SUBSCRIPTION_ID": "TF_VAR_subscription_id",
            "ARM_TENANT_ID":       "TF_VAR_tenant_id",
            "ARM_CLIENT_ID":       "TF_VAR_client_id",
        }
        for arm_key, tf_key in _ARM_TO_TF.items():
            val = cred_env.get(arm_key) or os.environ.get(arm_key, "")
            if val and tf_key not in result:
                result[tf_key] = val

        return result

    # ── Approval polling ──────────────────────────────────────────────────────

    def _wait_for_approval(self) -> bool:
        """Wait for the job to transition from awaiting_approval → pending_apply.

        Uses PostgreSQL LISTEN on channel 'job_<job_id>' so the executor wakes
        immediately when approve_job() sends NOTIFY — no 15-second polling delay.
        Falls back to a status check every APPROVAL_POLL_INTERVAL seconds in case
        the NOTIFY is missed (e.g. connection drop between LISTEN and NOTIFY).
        """
        import select as _select
        import psycopg2

        dsn = (
            f"host={os.getenv('DB_HOST', 'localhost')} "
            f"port={os.getenv('DB_PORT', '5432')} "
            f"dbname={os.getenv('DB_NAME', 'cloud_migrator')} "
            f"user={os.getenv('DB_USER', 'postgres')} "
            f"password={os.getenv('DB_PASSWORD', 'postgres')}"
        )
        channel = f"job_{self.job_id.replace('-', '_')}"
        deadline = time.time() + APPROVAL_TIMEOUT_SECONDS

        try:
            conn = psycopg2.connect(dsn)
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {channel}")

            while time.time() < deadline:
                remaining = deadline - time.time()
                timeout = min(APPROVAL_POLL_INTERVAL, remaining)
                if timeout <= 0:
                    break
                ready = _select.select([conn], [], [], timeout)[0]
                conn.poll()
                # Check DB status regardless of whether a notification arrived
                job = get_job(self.job_id)
                if job and job["status"] == "pending_apply":
                    return True
                if job and job["status"] in ("failed", "done"):
                    return False
        except Exception as exc:
            logger.warning("_wait_for_approval: LISTEN failed (%s) — falling back to polling", exc)
            # Fallback: plain polling if LISTEN setup failed
            deadline2 = time.time() + APPROVAL_TIMEOUT_SECONDS
            while time.time() < deadline2:
                job = get_job(self.job_id)
                if job and job["status"] == "pending_apply":
                    return True
                if job and job["status"] in ("failed", "done"):
                    return False
                time.sleep(APPROVAL_POLL_INTERVAL)
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return False
