"""
cleanup.py — Cleanup handler for incomplete Terraform deployments.

When a user closes the browser during `terraform apply`, the Azure resources
remain in a partially created state. This module detects such migrations and
either:
  A. Runs `terraform destroy` automatically (if state file exists)
  B. Marks the migration as "requires_cleanup" and alerts the admin

Triggered by:
  - API startup scan (checks for stuck "Deploying" migrations)
  - Worker callback on job failure at apply stage
  - Manual POST /api/v1/migrations/{id}/cleanup

Design principles:
  - NEVER auto-destroy without explicit confirmation for apply-stage failures
    (partial cloud state may exist — destroying blindly can cause data loss)
  - Log everything to audit trail
  - Always set deployment_status to "cleanup_required" for human review
"""
import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("Cleanup")

_PROJECT_ROOT = Path(__file__).parent.parent
_OUTPUT_DIR   = Path(os.environ.get("MIGRATION_OUTPUT_DIR", str(_PROJECT_ROOT / "output")))


async def scan_stuck_migrations(max_age_minutes: int = 90) -> list[str]:
    """Find migrations stuck in 'Deploying' or 'Health_Checking' for too long.

    A migration stuck for > max_age_minutes is considered interrupted
    (user closed browser, server restart, container crash, etc.)

    Returns list of migration IDs that need attention.
    """
    try:
        from configuration.database import async_session
        from data.repositories.migration_repository import MigrationRepository
        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        stuck = []

        async with async_session() as db:
            repo = MigrationRepository(db)
            # Get all migrations in active deploy states
            all_migrations = await repo.get_all(skip=0, limit=1000)
            for m in all_migrations:
                if m.status in ("Deploying", "Health_Checking"):
                    if m.updated_at and m.updated_at < cutoff:
                        stuck.append(m.id)
                        logger.warning(
                            "[Cleanup] Migration %s stuck in %s since %s (> %d min) — flagging",
                            m.id, m.status, m.updated_at.isoformat(), max_age_minutes,
                        )

        return stuck

    except Exception as exc:
        logger.error("[Cleanup] scan_stuck_migrations failed: %s", exc)
        return []


async def mark_cleanup_required(migration_id: str, reason: str) -> bool:
    """Mark a migration as requiring cleanup and alert admin via audit log.

    Does NOT automatically destroy resources — that requires human confirmation.
    """
    try:
        from configuration.database import async_session
        from data.repositories.migration_repository import MigrationRepository
        from services.audit_service import audit

        async with async_session() as db:
            repo = MigrationRepository(db)
            migration = await repo.get_by_id(migration_id)
            if not migration:
                return False

            await repo.update_by_id(migration_id, {
                "deployment_status": "cleanup_required",
                "errors": list(migration.errors or []) + [
                    f"[Cleanup required] {reason}"
                ],
            })

            await audit(
                db,
                action="migration.cleanup.required",
                resource_type="migration",
                resource_id=migration_id,
                resource_name=migration.repo_url,
                details={"reason": reason, "previous_status": migration.status},
                success=False,
                error=reason,
            )

        logger.warning(
            "[Cleanup] Migration %s marked as cleanup_required: %s",
            migration_id, reason,
        )
        return True

    except Exception as exc:
        logger.error("[Cleanup] mark_cleanup_required(%s) failed: %s", migration_id, exc)
        return False


def run_terraform_destroy(migration_id: str, work_dir: Optional[str] = None) -> dict:
    """Run `terraform destroy -auto-approve` on the migration's work directory.

    CAUTION: Only call this after explicit admin confirmation.
    Returns {"success": bool, "output": str, "error": str | None}
    """
    if not work_dir:
        # Try the standard run directory
        run_dir = _OUTPUT_DIR / "runs" / migration_id
        if not run_dir.exists():
            return {"success": False, "output": "", "error": f"Work directory not found: {run_dir}"}
        work_dir = str(run_dir)

    tf_state = Path(work_dir) / "terraform.tfstate"
    if not tf_state.exists():
        return {
            "success": False, "output": "",
            "error": "No terraform.tfstate found — resources may have been created "
                     "without a state file. Manual cleanup via Azure portal required.",
        }

    logger.warning("[Cleanup] Running terraform destroy for migration %s in %s", migration_id, work_dir)

    try:
        result = subprocess.run(
            ["terraform", "destroy", "-auto-approve", "-no-color"],
            cwd=work_dir,
            capture_output=True, text=True, timeout=600,  # 10 min max
            env={**os.environ, "TF_INPUT": "0"},
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr

        if success:
            logger.info("[Cleanup] terraform destroy succeeded for migration %s", migration_id)
        else:
            logger.error("[Cleanup] terraform destroy FAILED for migration %s:\n%s", migration_id, output[-2000:])

        return {
            "success": success,
            "output": output[-5000:],  # last 5000 chars
            "error": None if success else result.stderr[-1000:],
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": "terraform destroy timed out after 10 minutes"}
    except FileNotFoundError:
        return {"success": False, "output": "", "error": "terraform binary not found in PATH"}
    except Exception as exc:
        return {"success": False, "output": "", "error": str(exc)}


async def startup_cleanup_scan() -> None:
    """Run at API startup to detect interrupted deployments.

    Designed to be called once from app.py lifespan, non-blocking.
    """
    logger.info("[Cleanup] Startup scan for interrupted deployments…")
    stuck = await scan_stuck_migrations(max_age_minutes=90)

    if not stuck:
        logger.info("[Cleanup] No stuck migrations found.")
        return

    for migration_id in stuck:
        await mark_cleanup_required(
            migration_id,
            reason=(
                "Migration was in an active deployment state for more than 90 minutes "
                "without update — likely interrupted by a browser close, container restart, "
                "or network failure. Azure resources may be in a partial state. "
                "Admin review required before retrying."
            ),
        )

    logger.warning(
        "[Cleanup] %d migration(s) flagged as cleanup_required: %s",
        len(stuck), stuck,
    )
