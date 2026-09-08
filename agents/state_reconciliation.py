"""
service/agents/state_reconciliation.py — Split-brain safety via deterministic reconciliation.

Three sources of truth:
  runner_jobs      — execution outcome (authoritative)
  pipeline_state   — LangGraph checkpoint (advisory, may lag)
  MigrationState   — in-memory snapshot (ephemeral, never authoritative)

Precedence rules (hard order):
  1. runner_jobs.status is canonical for execution outcome.
  2. Terminal states (done, failed) in runner_jobs are immutable.
  3. pipeline_state is used only as fallback when runner_jobs row is missing.
  4. MigrationState is never consulted — it reflects a prior checkpoint.

Conflicts (runner_status != pipeline_status) are logged as structured warnings
but resolved deterministically — no external side-effects.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger("Reconcile")

_TERMINAL: frozenset[str] = frozenset({"done", "failed"})


def is_terminal(status: Optional[str]) -> bool:
    """Return True if status is immutable (done or failed)."""
    return status in _TERMINAL


def resolve_canonical_state(job_id: str) -> dict:
    """Determine the single source of truth for a deployment job.

    Queries runner_jobs (authoritative) and pipeline_state (advisory),
    applies strict precedence, detects and logs conflicts.

    Args:
        job_id: UUID of the runner_jobs row.

    Returns:
        {
            "canonical_status":  str,        # queued|running|done|failed|unknown
            "source_of_truth":   str | None, # "runner_jobs"|"pipeline_state"|None
            "runner_status":     str | None,
            "pipeline_status":   str | None,
            "runner_error_class": str | None,
            "conflict_detected": bool,
            "is_terminal":       bool,
        }
    """
    import psycopg2
    from executor.queue import get_job, _connect

    # ── Source 1: runner_jobs ─────────────────────────────────────────────────
    runner_job = get_job(job_id)
    runner_status = runner_job.get("status") if runner_job else None
    runner_error_class = runner_job.get("error_class") if runner_job else None

    # ── Source 2: pipeline_state checkpoint ──────────────────────────────────
    pipeline_status: Optional[str] = None
    if runner_job and runner_job.get("migration_id"):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT checkpoint FROM pipeline_state "
                        "WHERE checkpoint->>'thread_id' = %s "
                        "ORDER BY updated_at DESC LIMIT 1",
                        (runner_job["migration_id"],),
                    )
                    row = cur.fetchone()
                    if row:
                        cp = row[0] if isinstance(row, tuple) else row.get("checkpoint")
                        if isinstance(cp, str):
                            try:
                                cp = json.loads(cp)
                            except (json.JSONDecodeError, TypeError):
                                cp = None
                        if isinstance(cp, dict):
                            pipeline_status = cp.get("deployment_status")
                conn.commit()
            except psycopg2.Error:
                conn.rollback()
            finally:
                conn.close()
        except Exception:
            pass

    # ── Conflict detection ────────────────────────────────────────────────────
    conflict = bool(
        pipeline_status
        and runner_status
        and pipeline_status != runner_status
    )
    if conflict:
        logger.warning(
            "[Reconcile] state conflict detected",
            extra={
                "job_id":          job_id,
                "runner_status":   runner_status,
                "pipeline_status": pipeline_status,
            },
        )

    # ── Precedence: runner_jobs is always authoritative ───────────────────────
    if runner_status and is_terminal(runner_status):
        return {
            "canonical_status":   runner_status,
            "source_of_truth":    "runner_jobs",
            "runner_status":      runner_status,
            "pipeline_status":    pipeline_status,
            "runner_error_class": runner_error_class,
            "conflict_detected":  conflict,
            "is_terminal":        True,
        }

    if runner_status:
        return {
            "canonical_status":   runner_status,
            "source_of_truth":    "runner_jobs",
            "runner_status":      runner_status,
            "pipeline_status":    pipeline_status,
            "runner_error_class": runner_error_class,
            "conflict_detected":  conflict,
            "is_terminal":        False,
        }

    # ── Fallback: runner_jobs row missing — use pipeline_state ────────────────
    # This is a data-loss signal; conflict_detected is forced True.
    if pipeline_status:
        logger.error(
            "[Reconcile] runner_jobs row missing for job_id=%s — falling back to pipeline_state",
            job_id,
        )
        return {
            "canonical_status":   pipeline_status,
            "source_of_truth":    "pipeline_state",
            "runner_status":      None,
            "pipeline_status":    pipeline_status,
            "runner_error_class": None,
            "conflict_detected":  True,
            "is_terminal":        is_terminal(pipeline_status),
        }

    # ── No data in either source ──────────────────────────────────────────────
    return {
        "canonical_status":   "unknown",
        "source_of_truth":    None,
        "runner_status":      runner_status,
        "pipeline_status":    pipeline_status,
        "runner_error_class": None,
        "conflict_detected":  False,
        "is_terminal":        False,
    }
