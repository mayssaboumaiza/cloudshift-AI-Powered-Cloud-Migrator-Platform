"""
api/routers/v1/alerts_router.py — Unified alerts aggregator.

Aggregates LOW / MEDIUM / HIGH alerts from all migrations:
  - Checkov security failures (from iac_validation artifacts)
  - Infrastructure drift (from infra-health endpoint)
  - Resource health errors (from runner job status)
  - Intent violations (from migration artifacts)
  - Pipeline errors and warnings

GET /alerts           — all active alerts across all migrations
GET /alerts/summary   — counts only (badge number)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger("AlertsRouter")
router = APIRouter(tags=["ALERTS"])


# ── Severity config ───────────────────────────────────────────────────────────
_CHECKOV_SEVERITY_MAP = {
    "CRITICAL": "HIGH",
    "HIGH":     "HIGH",
    "MEDIUM":   "MEDIUM",
    "LOW":      "LOW",
    "UNKNOWN":  "LOW",
}

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_all_migrations() -> list[dict]:
    """Fetch basic migration data from the DB."""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(
        f"host={os.getenv('DB_HOST','localhost')} "
        f"port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
        f"user={os.getenv('DB_USER','postgres')} "
        f"password={os.getenv('DB_PASSWORD','postgres')}",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, repo_url, status, source_cloud, target_cloud,
                       artifacts, errors, warnings, updated_at
                FROM migrations
                WHERE status::text NOT IN ('Rejected', 'Exported')
                ORDER BY updated_at DESC
                LIMIT 50
            """)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _get_runner_failures() -> list[dict]:
    """Fetch recent failed runner jobs."""
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(
        f"host={os.getenv('DB_HOST','localhost')} "
        f"port={os.getenv('DB_PORT','5432')} "
        f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
        f"user={os.getenv('DB_USER','postgres')} "
        f"password={os.getenv('DB_PASSWORD','postgres')}",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, migration_id, status, error, error_class, created_at
                FROM runner_jobs
                WHERE status = 'failed'
                ORDER BY created_at DESC
                LIMIT 20
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


# ── Alert builders ─────────────────────────────────────────────────────────────

def _build_alerts_for_migration(migration: dict) -> list[dict]:
    alerts: list[dict] = []
    mid     = migration["id"]
    repo    = (migration.get("repo_url") or "").replace("https://github.com/", "")
    status  = migration.get("status", "")

    artifacts = migration.get("artifacts") or {}
    if isinstance(artifacts, str):
        try:
            artifacts = json.loads(artifacts)
        except Exception:
            artifacts = {}

    # ── 1. Checkov security failures ──────────────────────────────────────────
    iac_val = artifacts.get("iac_validation") or {}
    sec_scan = iac_val.get("security_scan") or {}
    for chk in (sec_scan.get("failed_checks") or []):
        raw_sev = (chk.get("severity") or "UNKNOWN").upper()
        sev = _CHECKOV_SEVERITY_MAP.get(raw_sev, "LOW")
        alerts.append({
            "id":            f"{mid}-checkov-{chk.get('check_id','')}",
            "migration_id":  mid,
            "repo":          repo,
            "severity":      sev,
            "category":      "security",
            "title":         f"Sécurité IaC : {chk.get('check_id','')}",
            "message":       chk.get("check_name") or chk.get("guideline") or "Security check failed",
            "resource":      chk.get("resource") or chk.get("file") or "",
            "action":        f"Ouvrir migration → onglet Sécurité IaC",
            "timestamp":     _ts(),
        })

    # ── 2. Terraform validate FAIL ────────────────────────────────────────────
    tf_val = iac_val.get("terraform_validation") or {}
    if tf_val.get("valid") is False:
        diags = tf_val.get("diagnostics") or []
        msg = (diags[0].get("summary") if diags else tf_val.get("error")) or "Terraform validate failed"
        alerts.append({
            "id":            f"{mid}-tf-validate",
            "migration_id":  mid,
            "repo":          repo,
            "severity":      "MEDIUM",
            "category":      "iac",
            "title":         "Terraform validate : FAIL",
            "message":       str(msg)[:200],
            "resource":      "",
            "action":        "Ouvrir migration → onglet Sécurité IaC",
            "timestamp":     _ts(),
        })

    # ── 3. Intent violations ───────────────────────────────────────────────────
    for issue in (artifacts.get("intent_issues") or []):
        sev = "HIGH" if "budget" in issue.lower() or "region" in issue.lower() else "MEDIUM"
        alerts.append({
            "id":            f"{mid}-intent-{hash(issue) & 0xFFFF}",
            "migration_id":  mid,
            "repo":          repo,
            "severity":      sev,
            "category":      "intent",
            "title":         "Violation d'intent",
            "message":       str(issue)[:200],
            "resource":      "",
            "action":        "Ouvrir migration → onglet Plan de migration",
            "timestamp":     _ts(),
        })

    # ── 4. Pipeline errors ─────────────────────────────────────────────────────
    for err in (migration.get("errors") or []):
        alerts.append({
            "id":            f"{mid}-error-{hash(err) & 0xFFFF}",
            "migration_id":  mid,
            "repo":          repo,
            "severity":      "HIGH",
            "category":      "pipeline",
            "title":         "Erreur pipeline",
            "message":       str(err)[:200],
            "resource":      "",
            "action":        "Ouvrir migration → onglet Pipeline",
            "timestamp":     _ts(),
        })

    # ── 5. Deployment status blocked ──────────────────────────────────────────
    dep_status = artifacts.get("deployment_status") or migration.get("deployment_status")
    if dep_status == "blocked":
        alerts.append({
            "id":            f"{mid}-deploy-blocked",
            "migration_id":  mid,
            "repo":          repo,
            "severity":      "MEDIUM",
            "category":      "deployment",
            "title":         "Déploiement bloqué",
            "message":       "Le déploiement est bloqué (credentials non validés ou erreur de préparation).",
            "resource":      "",
            "action":        "Ouvrir migration → onglet Déploiement",
            "timestamp":     _ts(),
        })

    # ── 6. Infrastructure health alerts (post-deployment UP→DOWN with cause) ────
    # Only generated when health_check_node has run (Deploying / Health_Checking / Completed)
    deploy_statuses = {"Deploying", "Health_Checking", "Completed", "Exported"}
    if status in deploy_statuses:
        health_checks = artifacts.get("health_checks") or {}
        health_status = artifacts.get("health_status", "")

        for check_key, check_val in health_checks.items():
            val_str = str(check_val)
            if val_str.startswith("❌") or val_str.startswith("⚠️"):
                is_critical = val_str.startswith("❌")
                # Extract the cause from the check message (strip the emoji prefix)
                cause = val_str.lstrip("❌⚠️✅ ").strip()

                _check_label_map = {
                    "deploy_script":     "Script de déploiement manquant",
                    "terraform_files":   "Fichiers Terraform absents",
                    "iac_validation":    "Validation IaC échouée",
                    "deployment_status": "Statut de déploiement",
                    "intent_validation": "Violation d'intent post-déploiement",
                    "budget":            "Dépassement de budget",
                }
                title = _check_label_map.get(check_key, f"Health check : {check_key}")

                alerts.append({
                    "id":           f"{mid}-health-{check_key}",
                    "migration_id": mid,
                    "repo":         repo,
                    "severity":     "HIGH" if is_critical else "MEDIUM",
                    "category":     "health",
                    "title":        f"Infra {'DOWN' if is_critical else 'DÉGRADÉE'} — {title}",
                    "message":      cause[:200],
                    "resource":     check_key,
                    "action":       "Ouvrir migration → onglet Health Report",
                    "timestamp":    _ts(),
                })

        # Deployment fully failed (runner failure class available)
        runner_failure_class = artifacts.get("runner_failure_class") or ""
        dep_status_art = artifacts.get("deployment_status") or ""
        if dep_status_art == "failed" and runner_failure_class:
            alerts.append({
                "id":           f"{mid}-infra-down",
                "migration_id": mid,
                "repo":         repo,
                "severity":     "HIGH",
                "category":     "health",
                "title":        "Infrastructure DOWN — déploiement échoué",
                "message":      f"Classe d'erreur : {runner_failure_class}. L'infrastructure cible n'a pas pu être provisionnée.",
                "resource":     "terraform apply",
                "action":       "Vérifier les logs Terraform dans l'onglet Déploiement",
                "timestamp":    _ts(),
            })

    # ── 7. IaC quality warnings ────────────────────────────────────────────────
    if artifacts.get("iac_quality_warning"):
        for warn in (migration.get("warnings") or []):
            if "[Intent]" in warn or "[Security]" in warn:
                alerts.append({
                    "id":            f"{mid}-warn-{hash(warn) & 0xFFFF}",
                    "migration_id":  mid,
                    "repo":          repo,
                    "severity":      "LOW",
                    "category":      "quality",
                    "title":         "Avertissement IaC",
                    "message":       str(warn).replace("[Intent]", "").replace("[Security]", "").strip()[:200],
                    "resource":      "",
                    "action":        "Ouvrir migration → onglet Terraform",
                    "timestamp":     _ts(),
                })

    return alerts


def _build_runner_alerts(failures: list[dict], migrations_by_id: dict) -> list[dict]:
    alerts: list[dict] = []
    for job in failures:
        mid  = job.get("migration_id", "")
        repo = (migrations_by_id.get(mid) or {}).get("repo_url", mid)
        repo = repo.replace("https://github.com/", "")
        alerts.append({
            "id":            f"{mid}-runner-{job.get('id','')}",
            "migration_id":  mid,
            "repo":          repo,
            "severity":      "HIGH",
            "category":      "deployment",
            "title":         f"Déploiement échoué — {job.get('error_class','RUNTIME')}",
            "message":       str(job.get("error") or "terraform apply failed")[:200],
            "resource":      "",
            "action":        "Ouvrir migration → onglet Déploiement",
            "timestamp":     str(job.get("created_at") or _ts()),
        })
    return alerts


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/alerts")
async def get_all_alerts(severity: str | None = None):
    """Return all active alerts across migrations, sorted by severity then time.

    Query params:
      severity — filter by HIGH / MEDIUM / LOW (optional)
    """
    def _run():
        try:
            migrations = _get_all_migrations()
        except Exception as exc:
            logger.warning("get_all_alerts: DB read failed: %s", exc)
            return {"alerts": [], "total": 0, "error": str(exc)}

        migrations_by_id = {m["id"]: m for m in migrations}
        all_alerts: list[dict] = []

        for m in migrations:
            all_alerts.extend(_build_alerts_for_migration(m))

        try:
            runner_failures = _get_runner_failures()
            all_alerts.extend(_build_runner_alerts(runner_failures, migrations_by_id))
        except Exception as exc:
            logger.debug("get_all_alerts: runner failures skipped: %s", exc)

        # Deduplicate by id
        seen: set[str] = set()
        deduped = []
        for a in all_alerts:
            if a["id"] not in seen:
                seen.add(a["id"])
                deduped.append(a)

        # Filter
        if severity:
            deduped = [a for a in deduped if a["severity"] == severity.upper()]

        # Sort: HIGH first, then MEDIUM, then LOW; within same level by timestamp desc
        deduped.sort(key=lambda a: (_SEVERITY_ORDER.get(a["severity"], 99), a.get("timestamp", "") or ""))

        counts = {
            "HIGH":   sum(1 for a in deduped if a["severity"] == "HIGH"),
            "MEDIUM": sum(1 for a in deduped if a["severity"] == "MEDIUM"),
            "LOW":    sum(1 for a in deduped if a["severity"] == "LOW"),
            "total":  len(deduped),
        }
        return {"alerts": deduped[:100], "counts": counts, "total": len(deduped)}

    try:
        return await asyncio.to_thread(_run)
    except Exception as exc:
        logger.error("get_all_alerts: %s", exc)
        return {"alerts": [], "counts": {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}, "total": 0}


@router.get("/alerts/summary")
async def get_alerts_summary():
    """Lightweight badge count only — called every 30s by the navbar bell."""
    def _run():
        try:
            migrations = _get_all_migrations()
        except Exception:
            return {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}

        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for m in migrations:
            for a in _build_alerts_for_migration(m):
                sev = a.get("severity", "LOW")
                counts[sev] = counts.get(sev, 0) + 1

        try:
            for job in _get_runner_failures():
                counts["HIGH"] = counts.get("HIGH", 0) + 1
        except Exception:
            pass

        total = sum(counts.values())
        return {**counts, "total": total}

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}
