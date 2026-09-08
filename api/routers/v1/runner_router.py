"""
api/routers/v1/runner_router.py — Terraform Runner job management endpoints.

Endpoints
─────────
GET  /runner/jobs/{job_id}               — job status + plan_summary + apply_outputs
GET  /runner/jobs/{job_id}/logs          — deployment log lines (paginated by ?after=<id>)
POST /runner/jobs/{job_id}/approve       — approve a job in awaiting_approval state
GET  /migrations/{migration_id}/runner   — get job for a migration (latest)

Security: /approve is guarded by the same INTERNAL_SERVICE_TOKEN as /secrets/retrieve.
          Log lines NEVER contain credential values.
"""
from __future__ import annotations

import logging
import os

import asyncio
import json

from fastapi import APIRouter, HTTPException, Header, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("RunnerRouter")

router = APIRouter(prefix="/runner", tags=["RUNNER"])
runner_migration_router = APIRouter(tags=["RUNNER"])


# ── Internal token guard (reuse same pattern as secrets_router) ───────────────

def _require_internal_token(x_internal_token: str | None = Header(default=None)) -> None:
    required = os.getenv("INTERNAL_SERVICE_TOKEN", "").strip()
    if not required:
        return
    if not x_internal_token or x_internal_token != required:
        raise HTTPException(
            status_code=403,
            detail="X-Internal-Token header is required to call this endpoint",
        )


# ── Response schemas ──────────────────────────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    migration_id: str
    status: str
    worker_id: str | None
    created_at: str
    claimed_at: str | None
    started_at: str | None
    completed_at: str | None
    error: str | None
    plan_summary: dict | None
    apply_outputs: dict | None
    attempt: int


class LogLineResponse(BaseModel):
    id: int
    stage: str
    stream: str
    line: str
    created_at: str


class ApproveResponse(BaseModel):
    approved: bool
    job_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_dt(dt) -> str | None:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    return str(dt)


def _job_response(row: dict) -> dict:
    return {
        "job_id": row["id"],
        "migration_id": row["migration_id"],
        "status": row["status"],
        "worker_id": row.get("worker_id"),
        "created_at": _fmt_dt(row["created_at"]),
        "claimed_at": _fmt_dt(row.get("claimed_at")),
        "started_at": _fmt_dt(row.get("started_at")),
        "completed_at": _fmt_dt(row.get("completed_at")),
        "error": row.get("error"),
        "plan_summary": row.get("plan_summary"),
        "apply_outputs": row.get("apply_outputs"),
        "attempt": row.get("attempt", 0),
    }


# ── GET /runner/jobs/{job_id} ─────────────────────────────────────────────────

@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    """Return job metadata. Never returns terraform output containing credentials."""
    from executor.queue import get_job
    row = get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Runner job '{job_id}' not found")
    return _job_response(row)


# ── GET /runner/jobs/{job_id}/logs ────────────────────────────────────────────

@router.get("/jobs/{job_id}/logs", response_model=list[LogLineResponse])
def get_job_logs(job_id: str, after: int = 0):
    """Return deployment log lines for a job, paginated by ?after=<row_id>.

    Lines NEVER contain plaintext credentials — the runner filters them at write time.
    """
    from executor.queue import get_logs, get_job
    # Verify job exists
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail=f"Runner job '{job_id}' not found")
    rows = get_logs(job_id, after_id=after)
    return [
        {
            "id": r["id"],
            "stage": r["stage"],
            "stream": r["stream"],
            "line": r["line"],
            "created_at": _fmt_dt(r["created_at"]),
        }
        for r in rows
    ]


# ── POST /runner/jobs/{job_id}/approve ───────────────────────────────────────

@router.post("/jobs/{job_id}/approve", response_model=ApproveResponse, status_code=status.HTTP_200_OK)
def approve_job(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Approve a Terraform plan in awaiting_approval state → unblocks APPLY.

    Guarded by X-Internal-Token in production.
    """
    _require_internal_token(x_internal_token)

    from executor.queue import approve_job as _approve, get_job
    row = get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Runner job '{job_id}' not found")
    if row["status"] not in ("awaiting_approval", "pending_apply"):
        raise HTTPException(
            status_code=409,
            detail=f"Job is in status '{row['status']}' — only awaiting_approval jobs can be approved",
        )

    approved = _approve(job_id)
    return {"approved": approved, "job_id": job_id}


# ── GET /migrations/{migration_id}/runner ─────────────────────────────────────

runner_migration_router = APIRouter(tags=["RUNNER"])


@runner_migration_router.get(
    "/migrations/{migration_id}/runner",
    response_model=JobStatusResponse,
)
def get_migration_runner_job(migration_id: str):
    """Return the latest runner job for a migration."""
    from executor.queue import get_job_for_migration
    row = get_job_for_migration(migration_id)
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No runner job found for migration '{migration_id}'",
        )
    return _job_response(row)


# ── POST /migrations/{migration_id}/runner-callback (L3-step3) ────────────────

class RunnerCallbackPayload(BaseModel):
    job_id: str
    status: str   # "done" or "failed"
    output: dict = {}


@runner_migration_router.post(
    "/migrations/{migration_id}/runner-callback",
    status_code=200,
)
async def runner_callback(
    migration_id: str,
    payload: RunnerCallbackPayload,
    x_internal_token: str | None = Header(default=None),
):
    """Called by the executor worker after TerraformRunner.run() completes.

    Resumes the interrupted LangGraph graph so it transitions from
    wait_runner_node → health_check → publish_github → END.
    Guarded by X-Internal-Token (same as /approve).
    """
    _require_internal_token(x_internal_token)

    from executor.queue import get_job, get_migration_thread_id

    job = get_job(payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Runner job '{payload.job_id}' not found")
    if job["migration_id"] != migration_id:
        raise HTTPException(status_code=400, detail="job_id does not belong to this migration")

    thread_id = get_migration_thread_id(migration_id)
    if not thread_id:
        raise HTTPException(status_code=404, detail=f"Migration '{migration_id}' not found")

    try:
        from pipeline.orchestrator import get_orchestrator
        result = await get_orchestrator().resume_after_runner(thread_id, payload.job_id)
        logger.info("runner_callback: graph resumed for migration=%s job=%s", migration_id, payload.job_id)

        try:
            from configuration.database import async_session
            from data.repositories.migration_repository import MigrationRepository
            from services.migration_service import MigrationService

            async with async_session() as _session:
                _service = MigrationService(MigrationRepository(_session))
                await _service.record_deploy_outcome(migration_id, result or {})
        except Exception as persist_exc:
            logger.error(
                "runner_callback: failed to persist deploy outcome migration=%s: %s",
                migration_id, persist_exc,
            )
    except Exception as exc:
        logger.error("runner_callback: graph resume error migration=%s: %s", migration_id, exc)

    return {"accepted": True, "migration_id": migration_id, "job_id": payload.job_id}


# ── GET /runner/jobs/{job_id}/logs/stream (SSE) ──────────────────────────────

@router.get("/jobs/{job_id}/logs/stream")
async def stream_job_logs(job_id: str, request: Request):
    """Server-Sent Events stream of Terraform deployment log lines.

    Polls the deployment_logs table every 0.5 s and pushes new lines as SSE
    events.  Stops automatically when the job reaches a terminal status
    (done | failed) and no more rows are expected.

    SSE event format:
        id: <row_id>
        data: {"id":1,"stage":"apply","stream":"stdout","line":"..."}

    Reconnect: EventSource sends Last-Event-ID header automatically, so the
    stream resumes from the last delivered row without duplicates.
    """
    last_id_header = request.headers.get("last-event-id", "0")
    try:
        cursor = int(last_id_header)
    except ValueError:
        cursor = 0

    TERMINAL = {"done", "failed", "error"}
    POLL_SEC  = 0.5
    IDLE_TTLS = 60          # seconds with no new lines before giving up on a terminal job
    MAX_EMPTY = int(IDLE_TTLS / POLL_SEC)

    async def _generate():
        nonlocal cursor
        from executor.queue import get_logs, get_job
        empty_count = 0

        # Heartbeat first so the connection is established immediately
        yield ": heartbeat\n\n"

        while True:
            if await request.is_disconnected():
                break

            rows = await asyncio.to_thread(get_logs, job_id, after_id=cursor)

            if rows:
                empty_count = 0
                for row in rows:
                    cursor = row["id"]
                    data = json.dumps({
                        "id":         row["id"],
                        "stage":      row.get("stage", ""),
                        "stream":     row.get("stream", "stdout"),
                        "line":       row.get("line", ""),
                        "created_at": str(row.get("created_at", "")),
                    })
                    yield f"id: {row['id']}\ndata: {data}\n\n"
            else:
                empty_count += 1
                # Check if job is terminal and we have exhausted all lines
                job = await asyncio.to_thread(get_job, job_id)
                if job and job.get("status") in TERMINAL and empty_count >= MAX_EMPTY:
                    # Send terminal event and close
                    yield f"data: {json.dumps({'type':'terminal','status':job['status'],'error':job.get('error')})}\n\n"
                    break
                # Heartbeat every 10 empty polls (~5 s) to keep the connection alive
                if empty_count % 10 == 0:
                    yield ": heartbeat\n\n"

            await asyncio.sleep(POLL_SEC)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /migrations/{migration_id}/github-actions ─────────────────────────────

@runner_migration_router.get("/migrations/{migration_id}/github-actions")
async def get_github_actions_status(migration_id: str):
    """Return the latest GitHub Actions workflow run for the migration repo.

    Reads github_repo_url from the migration artifacts, then queries the
    GitHub API using the stored token (Vault → plaintext) to return the
    latest workflow run status.
    """
    import asyncio, os, psycopg2, psycopg2.extras, json as _json

    def _fetch_artifacts(mid: str):
        conn = psycopg2.connect(
            f"host={os.getenv('DB_HOST','localhost')} port={os.getenv('DB_PORT','5432')} "
            f"dbname={os.getenv('DB_NAME','cloud_migrator')} "
            f"user={os.getenv('DB_USER','postgres')} password={os.getenv('DB_PASSWORD','postgres')}",
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT artifacts, github_token FROM migrations WHERE id = %s", (mid,)
                )
                return conn.cursor().fetchone() if False else cur.fetchone()
        finally:
            conn.close()

    row = await asyncio.to_thread(_fetch_artifacts, migration_id)
    if not row:
        raise HTTPException(status_code=404, detail="Migration not found")

    artifacts = row.get("artifacts") or {}
    if isinstance(artifacts, str):
        artifacts = _json.loads(artifacts)

    repo_url: str = artifacts.get("github_repo_url", "")
    if not repo_url:
        return {"available": False, "reason": "No GitHub repo URL in migration artifacts"}

    # Extract owner/repo from URL
    parts = repo_url.rstrip("/").replace("https://github.com/", "").split("/")
    if len(parts) < 2:
        return {"available": False, "reason": f"Cannot parse repo URL: {repo_url}"}
    owner, repo_name = parts[0], parts[1]

    # Fetch GitHub token from Vault
    token = ""
    try:
        from core.vault import get_credential_store
        store = get_credential_store()
        token = store.get(migration_id, "github_token") or ""
    except Exception:
        token = os.getenv("GITHUB_TOKEN", "")

    if not token:
        return {"available": False, "reason": "No GitHub token available"}

    # Call GitHub API for latest workflow runs
    try:
        from github import Github
        g = Github(token)
        gh_repo = g.get_repo(f"{owner}/{repo_name}")
        runs = gh_repo.get_workflow_runs()
        latest = None
        for run in runs:
            latest = run
            break

        if not latest:
            return {
                "available": True,
                "repo_url": repo_url,
                "repo_name": repo_name,
                "runs": [],
                "reason": "No workflow runs yet — push commits or trigger manually",
            }

        return {
            "available": True,
            "repo_url": repo_url,
            "repo_name": repo_name,
            "latest_run": {
                "id": latest.id,
                "name": latest.name,
                "status": latest.status,        # queued | in_progress | completed
                "conclusion": latest.conclusion, # success | failure | cancelled | None
                "url": latest.html_url,
                "branch": latest.head_branch,
                "created_at": latest.created_at.isoformat() if latest.created_at else None,
                "updated_at": latest.updated_at.isoformat() if latest.updated_at else None,
            },
        }
    except Exception as exc:
        logger.warning("github-actions endpoint: %s", exc)
        return {"available": False, "reason": str(exc)}
