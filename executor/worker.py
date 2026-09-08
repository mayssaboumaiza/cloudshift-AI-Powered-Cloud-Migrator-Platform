"""
executor/worker.py — Standalone Terraform Runner worker process.

Invoked as:  python -m executor.worker
             (or via the 'executor' Docker Compose service)

The worker runs an infinite poll loop:
  1. Claim the oldest pending job via SKIP LOCKED
  2. Execute TerraformRunner.run()
  3. Sleep briefly and repeat

Multiple worker replicas can run in parallel — SKIP LOCKED ensures each job
is claimed exactly once.

Environment variables:
  WORKER_ID            — unique identifier for this worker replica (default: hostname+pid)
  WORKER_POLL_INTERVAL — seconds to sleep between empty-queue polls (default: 5)
  API_BASE_URL         — base URL of the FastAPI service for runner callbacks (default: http://localhost:8000)
  API_KEY              — API key sent in X-API-Key header for the runner-callback endpoint
  DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD — PostgreSQL connection

TODO(T5-listen): Replace the sleep-based poll loop with PostgreSQL LISTEN so the
  worker wakes immediately when a new job is enqueued (enqueue() already sends
  NOTIFY 'new_migration_job').  Implementation sketch using psycopg2:

    conn = psycopg2.connect(_dsn())
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute("LISTEN new_migration_job")
    while not _SHUTDOWN:
        if select.select([conn], [], [], poll_interval) == ([], [], []):
            pass  # timeout — check anyway in case a notification was missed
        conn.poll()
        while conn.notifies:
            notify = conn.notifies.pop(0)
            # Try to claim the job referenced by notify.payload
            ...
        claimed = claim_next(worker_id)
        if claimed:
            job_id, manifest = claimed
            ...  # run as today

  Requires: import select at the top of the file.
  Note: the SELECT … FOR UPDATE SKIP LOCKED in claim_next() remains correct
  even with LISTEN — it prevents two workers from claiming the same job when
  a single NOTIFY wakes multiple replicas.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import time

from executor.queue import claim_next
from executor.runner import TerraformRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("RunnerWorker")

_SHUTDOWN = False


def _handle_signal(signum, frame):
    global _SHUTDOWN
    logger.info("RunnerWorker: received signal %s — shutting down after current job", signum)
    _SHUTDOWN = True


def _worker_id() -> str:
    custom = os.getenv("WORKER_ID", "").strip()
    if custom:
        return custom
    return f"{socket.gethostname()}-{os.getpid()}"


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    worker_id = _worker_id()
    poll_interval = int(os.getenv("WORKER_POLL_INTERVAL", "5"))
    logger.info("RunnerWorker started — worker_id=%s poll_interval=%ss", worker_id, poll_interval)

    # Continuous post-deployment infra monitoring (daemon thread). Re-checks live
    # Azure state of deployed migrations and raises alerts even when no UI is open.
    try:
        from executor.health_poller import start_health_poller
        start_health_poller()
    except Exception as exc:
        logger.warning("RunnerWorker: could not start health poller: %s", exc)

    while not _SHUTDOWN:
        try:
            claimed = claim_next(worker_id)
        except Exception as exc:
            logger.error("RunnerWorker: claim_next error: %s — retrying in %ss", exc, poll_interval)
            time.sleep(poll_interval)
            continue

        if claimed is None:
            time.sleep(poll_interval)
            continue

        job_id, manifest = claimed
        logger.info(
            "RunnerWorker: claimed job=%s migration=%s provider=%s",
            job_id, manifest.migration_id, manifest.provider,
        )

        try:
            runner = TerraformRunner(job_id=job_id, manifest=manifest, worker_id=worker_id)
            runner.run()
        except Exception as exc:
            logger.exception("RunnerWorker: TerraformRunner raised outside run(): job=%s", job_id)

        # L3-step4: Call the runner-callback endpoint so the LangGraph graph resumes
        # automatically after terraform apply completes.
        try:
            import httpx
            from executor.queue import get_job as _get_job
            _job = _get_job(job_id)
            _status = (_job["status"] if _job else "failed")
            _base = os.getenv("API_BASE_URL", "http://localhost:8000")
            _token = os.getenv("INTERNAL_SERVICE_TOKEN", "")
            _headers = {"X-Internal-Token": _token} if _token else {}
            httpx.post(
                f"{_base}/api/v1/migrations/{manifest.migration_id}/runner-callback",
                json={
                    "job_id": job_id,
                    "status": _status,
                    "output": (_job.get("apply_outputs") or {}) if _job else {},
                },
                headers=_headers,
                timeout=120,
            )
            logger.info(
                "RunnerWorker: callback sent job=%s migration=%s status=%s",
                job_id, manifest.migration_id, _status,
            )
        except Exception as cb_exc:
            logger.warning(
                "RunnerWorker: callback failed job=%s: %s", job_id, cb_exc
            )

        if _SHUTDOWN:
            break

    logger.info("RunnerWorker: shutdown complete")


if __name__ == "__main__":
    main()
