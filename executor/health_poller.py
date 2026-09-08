"""
executor/health_poller.py — Continuous post-deployment infra health monitoring.

Runs as a daemon thread inside the executor worker process. Every
HEALTH_POLL_INTERVAL seconds it re-checks the live Azure state of every
deployed migration and:

  1. re-queries Azure ARM → provisioning_state per resource
  2. detects drift (tfstate vs live)
  3. rewrites output/<id>/infra_health.json  (served by /infra-health?cached=true)
  4. writes a `health_checks` map + `health_status` into migrations.artifacts so the
     EXISTING alerts aggregator (api/routers/v1/alerts_router.py) raises HIGH/MEDIUM
     "Infra DOWN" alerts in the navbar bell — even when no UI tab is open.

This is what makes the monitoring AUTONOMOUS: the snapshot written once by the
runner right after apply is no longer the only source of truth.

All work is best-effort and wrapped in try/except — a failure here never affects
deployments.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("HealthPoller")

# Migration statuses that mean "infra is deployed and worth monitoring".
_MONITORED_STATUSES = ("Deploying", "Health_Checking", "Completed", "Exported")


def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST', 'localhost')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'cloud_migrator')} "
        f"user={os.getenv('DB_USER', 'postgres')} "
        f"password={os.getenv('DB_PASSWORD', 'postgres')}"
    )


def _list_deployed_migrations() -> list[dict]:
    """Return migrations whose infra is deployed (id + current artifacts)."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, artifacts
                FROM migrations
                WHERE status::text = ANY(%s)
                ORDER BY updated_at DESC
                LIMIT 50
                """,
                (list(_MONITORED_STATUSES),),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _persist_health_artifacts(migration_id: str, health_checks: dict, health_status: str) -> None:
    """Merge health_checks/health_status into migrations.artifacts (JSONB)."""
    import psycopg2

    conn = psycopg2.connect(_dsn())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # COALESCE so we merge onto whatever artifacts already exist.
            cur.execute(
                """
                UPDATE migrations
                SET artifacts = COALESCE(artifacts, '{}'::jsonb)
                    || jsonb_build_object(
                           'health_checks', %s::jsonb,
                           'health_status', %s::jsonb,
                           'health_checked_at', %s::jsonb
                       )
                WHERE id = %s
                """,
                (
                    json.dumps(health_checks),
                    json.dumps(health_status),
                    json.dumps(datetime.now(timezone.utc).isoformat()),
                    migration_id,
                ),
            )
    finally:
        conn.close()


def _arm_env_for(migration_id: str) -> dict | None:
    """Fetch ARM credentials from Vault (same path as the deploy stage)."""
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
        logger.debug("_arm_env_for %s: %s", migration_id, exc)
        return None


def _check_one(migration_id: str) -> None:
    """Re-check a single migration's infra and persist results."""
    from services.infra_monitor import (
        get_deployed_resources,
        check_resource_health,
        detect_drift,
    )

    resources = get_deployed_resources(migration_id)
    if not resources:
        return  # not deployed (no tfstate) — nothing to monitor

    arm_env = _arm_env_for(migration_id)
    if not arm_env or not arm_env.get("ARM_SUBSCRIPTION_ID"):
        return  # credentials gone (e.g. destroyed) — skip silently

    health = check_resource_health(resources, arm_env)
    drifts = detect_drift(resources, health)

    error_count = sum(1 for r in health if r.get("health_status") == "error")
    notfound_count = sum(1 for r in health if r.get("health_status") == "not_found")
    healthy_count = sum(1 for r in health if r.get("health_status") == "healthy")

    # ── Build the health_checks map consumed by alerts_router (❌ = HIGH / ⚠️ = MEDIUM)
    health_checks: dict[str, str] = {}
    for r in health:
        st = r.get("health_status")
        if st in ("error", "not_found"):
            name = r.get("display_name") or r.get("name") or r.get("type")
            prov = r.get("provisioning_state") or st
            health_checks[f"resource_{name}"] = f"❌ {name} is {prov} (was deployed)"
    for d in drifts:
        rn = d.get("resource_name") or d.get("resource_type")
        health_checks[f"drift_{rn}"] = f"⚠️ {rn} drifted from tfstate"

    if error_count or notfound_count:
        health_status = "CRITICAL"
    elif drifts:
        health_status = "DEGRADED"
    else:
        health_status = "HEALTHY"

    # ── 1. Rewrite the cached snapshot file (served by /infra-health?cached=true)
    try:
        from core.paths import get_output_dir

        payload = {
            "migration_id":      migration_id,
            "tfstate_available": True,
            "health_checks":     True,
            "checked_at":        datetime.now(timezone.utc).isoformat(),
            "resources":         health,
            "drift_detected":    drifts,
            "metrics":           {},
            "resource_count":    len(resources),
            "summary": {
                "total":     len(health),
                "healthy":   healthy_count,
                "updating":  sum(1 for r in health if r.get("health_status") in ("updating", "creating", "deleting")),
                "error":     error_count,
                "not_found": notfound_count,
                "unknown":   sum(1 for r in health if r.get("health_status") == "unknown"),
            },
            "drift_count": len(drifts),
        }
        out_file = get_output_dir(migration_id) / "infra_health.json"
        out_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("_check_one %s: snapshot write failed: %s", migration_id, exc)

    # ── 2. Persist into DB artifacts so the navbar alert bell sees it
    try:
        _persist_health_artifacts(migration_id, health_checks, health_status)
    except Exception as exc:
        logger.debug("_check_one %s: artifact persist failed: %s", migration_id, exc)

    if error_count or notfound_count:
        logger.warning(
            "HealthPoller: migration=%s INFRA DEGRADED — %d error / %d not_found / %d healthy",
            migration_id, error_count, notfound_count, healthy_count,
        )


def _poll_loop(interval: int, stop_event: threading.Event) -> None:
    logger.info("HealthPoller started — interval=%ss", interval)
    while not stop_event.is_set():
        try:
            for mig in _list_deployed_migrations():
                if stop_event.is_set():
                    break
                try:
                    _check_one(str(mig["id"]))
                except Exception as exc:
                    logger.debug("HealthPoller: check failed for %s: %s", mig.get("id"), exc)
        except Exception as exc:
            logger.warning("HealthPoller: poll cycle failed: %s", exc)
        stop_event.wait(interval)
    logger.info("HealthPoller: stopped")


def start_health_poller(stop_event: threading.Event | None = None) -> threading.Event:
    """Launch the health poller as a daemon thread. Returns its stop Event.

    Disabled by setting HEALTH_POLL_ENABLED=0. Interval via HEALTH_POLL_INTERVAL
    (seconds, default 300 = 5 min).
    """
    if os.getenv("HEALTH_POLL_ENABLED", "1") == "0":
        logger.info("HealthPoller disabled (HEALTH_POLL_ENABLED=0)")
        return stop_event or threading.Event()

    interval = int(os.getenv("HEALTH_POLL_INTERVAL", "300"))
    ev = stop_event or threading.Event()
    t = threading.Thread(target=_poll_loop, args=(interval, ev), name="health-poller", daemon=True)
    t.start()
    return ev
