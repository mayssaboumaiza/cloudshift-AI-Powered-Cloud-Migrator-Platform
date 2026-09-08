"""
executor/queue.py — PostgreSQL-backed job queue for TerraformRunner.

Uses SELECT … FOR UPDATE SKIP LOCKED so multiple workers can safely claim
jobs in parallel without distributed locks or Redis.

All DB access is synchronous psycopg2 (not asyncpg) so the worker can run a
plain asyncio event loop without a second async DB pool.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from executor.manifest import DeploymentManifest

logger = logging.getLogger("RunnerQueue")

# ── DSN from env ──────────────────────────────────────────────────────────────

def _dsn() -> str:
    return (
        f"host={os.getenv('DB_HOST', 'localhost')} "
        f"port={os.getenv('DB_PORT', '5432')} "
        f"dbname={os.getenv('DB_NAME', 'cloud_migrator')} "
        f"user={os.getenv('DB_USER', 'postgres')} "
        f"password={os.getenv('DB_PASSWORD', 'postgres')}"
    )


def _connect() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(_dsn(), cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


# ── Enqueue ───────────────────────────────────────────────────────────────────

def enqueue(manifest: DeploymentManifest) -> str:
    """Insert a new job into the queue. Returns the job_id (UUID).

    Called by the LangGraph enqueue_deploy node (sync context via
    _run_async_from_sync or plain sync call from graph.py).

    Raises if a job already exists for this migration (unique constraint).
    """
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runner_jobs
                    (id, migration_id, manifest, status, created_at, attempt)
                VALUES
                    (%s, %s, %s, 'pending', %s, 0)
                """,
                (job_id, manifest.migration_id, psycopg2.extras.Json(manifest.model_dump()), now),
            )
            # T5: Notify any LISTEN-based workers that a new job is available.
            # pg_notify fires when this transaction commits, so no duplicate
            # notifications are sent on rollback.
            cur.execute("SELECT pg_notify('new_migration_job', %s)", (job_id,))
        conn.commit()
        logger.info("enqueue: job=%s migration=%s", job_id, manifest.migration_id)
        return job_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Claim (SKIP LOCKED) ───────────────────────────────────────────────────────

def claim_next(worker_id: str) -> Optional[tuple[str, DeploymentManifest]]:
    """Atomically claim the oldest pending job.

    Returns (job_id, manifest) or None if the queue is empty.
    Uses SKIP LOCKED so two workers never race on the same row.
    """
    now = datetime.now(timezone.utc)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, manifest
                FROM runner_jobs
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
            )
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return None

            job_id = row["id"]
            manifest_data = row["manifest"]

            cur.execute(
                """
                UPDATE runner_jobs
                SET status = 'claimed', worker_id = %s, claimed_at = %s
                WHERE id = %s
                """,
                (worker_id, now, job_id),
            )
        conn.commit()
        manifest = DeploymentManifest.model_validate(manifest_data)
        logger.info("claim_next: worker=%s claimed job=%s migration=%s", worker_id, job_id, manifest.migration_id)
        return job_id, manifest
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Status transitions ────────────────────────────────────────────────────────

def set_status(
    job_id: str,
    status: str,
    *,
    error: str | None = None,
    plan_summary: dict | None = None,
    apply_outputs: dict | None = None,
    error_class: str | None = None,
    error_detail: dict | None = None,
) -> None:
    """Update a job's status and optional result fields."""
    now = datetime.now(timezone.utc)
    terminal = status in ("done", "failed")

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runner_jobs
                SET status        = %s,
                    started_at    = CASE WHEN status = 'claimed' THEN %s ELSE started_at END,
                    completed_at  = CASE WHEN %s THEN %s ELSE completed_at END,
                    error         = COALESCE(%s, error),
                    plan_summary  = COALESCE(%s, plan_summary),
                    apply_outputs = COALESCE(%s, apply_outputs),
                    error_class   = COALESCE(%s, error_class),
                    error_detail  = COALESCE(%s, error_detail)
                WHERE id = %s
                """,
                (
                    status,
                    now,
                    terminal, now,
                    error,
                    psycopg2.extras.Json(plan_summary) if plan_summary else None,
                    psycopg2.extras.Json(apply_outputs) if apply_outputs else None,
                    error_class,
                    psycopg2.extras.Json(error_detail) if error_detail else None,
                    job_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def timeout_fail_job(job_id: str, error_msg: str) -> bool:
    """C-1 / C-4: Force a job to 'failed' using a conditional UPDATE.

    Only transitions if the job is NOT already in a terminal state
    ('done' or 'failed').  This prevents a late-arriving runner 'done'
    write from being clobbered, and frees the partial-unique-index slot
    so a retry enqueue can proceed.

    Returns True if the row was updated, False if it was already terminal.
    """
    now = datetime.now(timezone.utc)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runner_jobs
                SET status       = 'failed',
                    completed_at = %s,
                    error        = %s,
                    error_class  = COALESCE(error_class, 'ENVIRONMENTAL')
                WHERE id = %s
                  AND status NOT IN ('done', 'failed')
                """,
                (now, error_msg, job_id),
            )
            updated = cur.rowcount
        conn.commit()
        logger.info(
            "timeout_fail_job: job=%s updated=%s msg=%r", job_id, updated > 0, error_msg[:80]
        )
        return updated > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_job(job_id: str) -> Optional[dict]:
    """Fetch job metadata (no manifest values — only status/timestamps/error)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, migration_id, status, worker_id, created_at, claimed_at, "
                "started_at, completed_at, error, plan_summary, apply_outputs, attempt, "
                "error_class, error_detail "
                "FROM runner_jobs WHERE id = %s",
                (job_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_job_for_migration(migration_id: str) -> Optional[dict]:
    """Fetch the most recent job for a migration_id."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, migration_id, status, worker_id, created_at, claimed_at, "
                "started_at, completed_at, error, plan_summary, apply_outputs, attempt, "
                "error_class, error_detail "
                "FROM runner_jobs WHERE migration_id = %s ORDER BY created_at DESC LIMIT 1",
                (migration_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_migration_thread_id(migration_id: str) -> Optional[str]:
    """Fetch the LangGraph thread_id for a migration (used by runner-callback to resume graph)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT thread_id FROM migrations WHERE id = %s", (migration_id,))
            row = cur.fetchone()
            return row["thread_id"] if row else None
    finally:
        conn.close()


def approve_job(job_id: str) -> bool:
    """Transition a job from awaiting_approval → pending_apply.

    Sends NOTIFY on channel 'job_<job_id>' so the executor's LISTEN wakes
    immediately instead of waiting for the next poll interval.
    Returns True if the transition succeeded, False if already past that state.
    """
    channel = f"job_{job_id.replace('-', '_')}"
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runner_jobs
                SET status = 'pending_apply'
                WHERE id = %s AND status = 'awaiting_approval'
                """,
                (job_id,),
            )
            updated = cur.rowcount
            if updated > 0:
                cur.execute(f"SELECT pg_notify('{channel}', 'approved')")
        conn.commit()
        return updated > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_logs(job_id: str, after_id: int = 0) -> list[dict]:
    """Fetch deployment log lines for a job, optionally paginated by row id."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stage, stream, line, created_at "
                "FROM deployment_logs "
                "WHERE job_id = %s AND id > %s "
                "ORDER BY id ASC",
                (job_id, after_id),
            )
            return cur.fetchall()
    finally:
        conn.close()


def append_log(job_id: str, stage: str, line: str, stream: str = "stdout") -> None:
    """Append a single log line. Called by the runner subprocess — never logs credentials."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO deployment_logs (job_id, stage, stream, line, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (job_id, stage, stream, line, datetime.now(timezone.utc)),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.warning("append_log: failed to write log line job=%s stage=%s", job_id, stage)
    finally:
        conn.close()
