"""
api/routers/v1/infra_health_router.py — Post-deployment infrastructure monitoring.

Endpoints
─────────
GET /migrations/{id}/infra-health   — full health snapshot (ARM + metrics + drift)
GET /migrations/{id}/infra-metrics  — Azure Monitor metrics only (lighter, faster)
GET /migrations/{id}/infra-drift    — drift detection only (tfstate vs live ARM)
"""
from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("InfraHealthRouter")

router = APIRouter(tags=["INFRA_HEALTH"])


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_arm_env(migration_id: str) -> dict | None:
    """Fetch ARM credentials from Vault for a migration.

    Returns a dict with ARM_CLIENT_ID / ARM_CLIENT_SECRET /
    ARM_TENANT_ID / ARM_SUBSCRIPTION_ID, or None if unavailable.
    """
    try:
        from services.credentials.broker import get_secret_broker
        broker = get_secret_broker()
        data = broker.get(broker.migration_path(migration_id, "azure"))
        if not data:
            return None
        return {
            "ARM_CLIENT_ID":       data.get("client_id", ""),
            "ARM_CLIENT_SECRET":   data.get("client_secret", ""),
            "ARM_TENANT_ID":       data.get("tenant_id", ""),
            "ARM_SUBSCRIPTION_ID": data.get("subscription_id", ""),
        }
    except Exception as exc:
        logger.warning("_get_arm_env %s: %s", migration_id, exc)
        return None


def _no_tfstate_response(migration_id: str) -> dict:
    return {
        "migration_id":      migration_id,
        "tfstate_available": False,
        "resources":         [],
        "summary":           {"total": 0, "healthy": 0, "updating": 0, "error": 0, "not_found": 0},
        "metrics":           {},
        "drift_detected":    [],
        "message":           (
            "terraform.tfstate not found. "
            "Run terraform apply first (deployment must complete successfully)."
        ),
    }


def _no_credentials_response(migration_id: str, resources: list) -> dict:
    return {
        "migration_id":      migration_id,
        "tfstate_available": True,
        "health_checks":     False,
        "resources":         resources,
        "summary":           {"total": len(resources), "healthy": 0, "updating": 0, "error": 0, "not_found": 0},
        "metrics":           {},
        "drift_detected":    [],
        "message":           "Azure credentials not found in Vault — cannot call ARM API.",
    }


# ── GET /migrations/{id}/infra-health ────────────────────────────────────────

@router.get("/migrations/{migration_id}/infra-health")
async def get_infra_health(
    migration_id: str,
    timespan_minutes: int = 60,
    cached: bool = False,
):
    """Full infrastructure health snapshot.

    With cached=true: returns the infra_health.json written by the runner right
    after apply (fast, no ARM call needed).

    Without cached (default): live query against Azure ARM + Monitor APIs.
      1. Parse terraform.tfstate → list of deployed resources
      2. Call Azure ARM → provisioning_state per resource
      3. Fetch Azure Monitor metrics (CPU, memory, connections…)
      4. Detect drift between tfstate and live ARM response
    """
    if cached:
        import json
        from pathlib import Path
        from core.paths import get_output_dir
        health_file = get_output_dir(migration_id) / "infra_health.json"
        if health_file.exists():
            try:
                return json.loads(health_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("get_infra_health: cached file read error: %s", exc)
        # Fall through to live query if file is missing or unreadable

    from services.infra_monitor import (
        get_deployed_resources,
        check_resource_health,
        get_metrics,
        detect_drift,
    )

    def _run() -> dict:
        resources = get_deployed_resources(migration_id)
        if not resources:
            return _no_tfstate_response(migration_id)

        arm_env = _get_arm_env(migration_id)
        if not arm_env:
            return _no_credentials_response(migration_id, resources)

        health  = check_resource_health(resources, arm_env)
        metrics = get_metrics(resources, arm_env, timespan_minutes=timespan_minutes)
        drifts  = detect_drift(resources, health)

        summary = {
            "total":     len(health),
            "healthy":   sum(1 for r in health if r["health_status"] == "healthy"),
            "updating":  sum(1 for r in health if r["health_status"] in ("updating", "creating", "deleting")),
            "error":     sum(1 for r in health if r["health_status"] == "error"),
            "not_found": sum(1 for r in health if r["health_status"] == "not_found"),
            "unknown":   sum(1 for r in health if r["health_status"] == "unknown"),
        }

        return {
            "migration_id":      migration_id,
            "tfstate_available": True,
            "health_checks":     True,
            "resources":         health,
            "metrics":           metrics,
            "drift_detected":    drifts,
            "summary":           summary,
            "drift_count":       len(drifts),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        logger.error("get_infra_health %s: %s", migration_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /migrations/{id}/infra-metrics ───────────────────────────────────────

@router.get("/migrations/{migration_id}/infra-metrics")
async def get_infra_metrics(
    migration_id: str,
    timespan_minutes: int = 60,
):
    """Fetch Azure Monitor metrics only (no ARM health call — faster)."""
    from services.infra_monitor import get_deployed_resources, get_metrics

    def _run() -> dict:
        resources = get_deployed_resources(migration_id)
        if not resources:
            return {"migration_id": migration_id, "metrics": {}, "message": "No tfstate found"}

        arm_env = _get_arm_env(migration_id)
        if not arm_env:
            return {"migration_id": migration_id, "metrics": {}, "message": "No Azure credentials in Vault"}

        metrics = get_metrics(resources, arm_env, timespan_minutes=timespan_minutes)
        return {"migration_id": migration_id, "metrics": metrics, "resource_count": len(resources)}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        logger.error("get_infra_metrics %s: %s", migration_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /migrations/{id}/infra-drift ─────────────────────────────────────────

@router.get("/migrations/{migration_id}/infra-drift")
async def get_infra_drift(migration_id: str):
    """Detect configuration drift between terraform.tfstate and live ARM state."""
    from services.infra_monitor import get_deployed_resources, check_resource_health, detect_drift

    def _run() -> dict:
        resources = get_deployed_resources(migration_id)
        if not resources:
            return {"migration_id": migration_id, "drifts": [], "message": "No tfstate found"}

        arm_env = _get_arm_env(migration_id)
        if not arm_env:
            return {"migration_id": migration_id, "drifts": [], "message": "No Azure credentials in Vault"}

        health = check_resource_health(resources, arm_env)
        drifts = detect_drift(resources, health)
        return {
            "migration_id":   migration_id,
            "drifts":         drifts,
            "drift_count":    len(drifts),
            "resources_checked": len(resources),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        logger.error("get_infra_drift %s: %s", migration_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── POST /migrations/{id}/infra-destroy ──────────────────────────────────────

@router.post("/migrations/{migration_id}/infra-destroy")
async def destroy_infra(migration_id: str):
    """Queue a terraform destroy job for this migration.

    Enqueues a RunnerJob with destroy=True so the executor runs
    `terraform destroy -auto-approve` against the deployed infrastructure.
    Credentials are fetched JIT from Vault (same as apply).
    """
    import json
    from pathlib import Path

    def _run() -> dict:
        from executor.manifest import DeploymentManifest
        from executor.queue import enqueue, get_job_for_migration
        from services.infra_monitor import get_deployed_resources

        resources = get_deployed_resources(migration_id)
        if not resources:
            raise HTTPException(status_code=404, detail="No tfstate found — nothing to destroy")

        arm_env = _get_arm_env(migration_id)
        if not arm_env:
            raise HTTPException(status_code=400, detail="Azure credentials not found in Vault")

        # work_dir must be the Terraform runs directory (contains .tf files + tfstate)
        # get_output_dir() points to output/<uuid>/ (Agent 03 artifacts — no .tf files)
        # The runner writes Terraform files to output/runs/<uuid>/
        from core.paths import get_output_dir
        runs_dir = get_output_dir(migration_id).parent / "runs" / migration_id
        if runs_dir.exists():
            work_dir = str(runs_dir)
        else:
            work_dir = str(get_output_dir(migration_id))
        if not Path(work_dir).exists():
            raise HTTPException(status_code=404, detail=f"work_dir not found: {work_dir}")

        # Build secret_refs same as deploy
        secret_refs = {"azure": f"migrations/{migration_id}/azure"}

        manifest = DeploymentManifest(
            migration_id=migration_id,
            thread_id=migration_id,
            work_dir=work_dir,
            tf_files=[f.name for f in Path(work_dir).glob("*.tf")],
            provider="azure",
            target_region="",
            secret_refs=secret_refs,
            auto_approve=True,
            destroy=True,
        )

        existing = get_job_for_migration(migration_id)
        if existing and existing.get("status") not in ("done", "failed"):
            raise HTTPException(status_code=409, detail=f"A runner job is already active: {existing['status']}")

        job_id = enqueue(manifest.model_dump())
        logger.info("infra_destroy: enqueued destroy job=%s migration=%s", job_id, migration_id)
        return {"job_id": job_id, "status": "queued", "action": "destroy"}

    try:
        return await asyncio.to_thread(_run)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("infra_destroy %s: %s", migration_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))
