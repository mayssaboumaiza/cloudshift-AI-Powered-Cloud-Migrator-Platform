"""
executor/stages.py — Stage implementations for the TerraformRunner pipeline.

Stage order:  INIT → PLAN → (AWAITING_APPROVAL) → APPLY → VERIFY

Each stage function receives a StageContext and returns a StageResult.
Stages communicate through StageResult.data dict — they never touch the DB
directly for job status (the runner does that).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("RunnerStages")

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class StageContext:
    """Passed to every stage function."""
    job_id: str
    work_dir: str
    env: dict[str, str]          # subprocess env (includes TF_* vars, cloud credentials)
    log: "RunnerLogStream"        # dual-write logger (imported lazily to avoid circular)
    manifest: "DeploymentManifest"


@dataclass
class StageResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stage_log_path(work_dir: str, stage: str) -> Path:
    """Return the path to the per-stage terraform log file."""
    return Path(work_dir) / f"terraform_{stage}.log"


def _append_to_stage_log(work_dir: str, stage: str, text: str) -> None:
    """Append text to the per-stage log file (best-effort, never raises)."""
    try:
        _stage_log_path(work_dir, stage).open("a", encoding="utf-8").write(text)
    except Exception:
        pass


def _run(
    args: list[str],
    ctx: StageContext,
    stage: str,
    *,
    capture_stdout: bool = False,
    timeout: int = 600,
) -> tuple[bool, str, str]:
    """Run a subprocess, streaming stderr to the log writer AND to a .log file.

    Returns (success, stdout_text, stderr_text).
    stderr is always written line-by-line to ctx.log AND captured for classification.
    stdout is written too unless capture_stdout=True (needed for json output).

    Each stage writes to  <work_dir>/terraform_<stage>.log  on the host filesystem.
    The master log at  <work_dir>/deployment.log  aggregates all stages.
    """
    cmd_str = "$ " + " ".join(args)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = f"\n{'='*60}\n[{ts}] {cmd_str}\n{'='*60}\n"

    ctx.log.write(cmd_str, stream="system")
    # Write to per-stage log and master deployment log
    for log_stage in (stage, "deployment"):
        _append_to_stage_log(ctx.work_dir, log_stage, header)

    try:
        proc = subprocess.Popen(
            args,
            cwd=ctx.work_dir,
            env=ctx.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        import select as _select
        fds = [proc.stdout, proc.stderr]
        while fds:
            readable, _, _ = _select.select(fds, [], [], 1.0)
            for f in readable:
                line = f.readline()
                if not line:
                    fds.remove(f)
                    continue
                stripped = line.rstrip("\n")
                if f is proc.stderr:
                    ctx.log.write(stripped, stream="stderr")
                    stderr_lines.append(stripped)
                    file_line = f"[stderr] {stripped}\n"
                else:
                    stdout_lines.append(stripped)
                    if not capture_stdout:
                        ctx.log.write(stripped, stream="stdout")
                    file_line = f"{stripped}\n"
                # Write every line to both per-stage and master log
                for log_stage in (stage, "deployment"):
                    _append_to_stage_log(ctx.work_dir, log_stage, file_line)

        proc.wait(timeout=timeout)
        exit_line = f"\nExit code: {proc.returncode}\n"
        for log_stage in (stage, "deployment"):
            _append_to_stage_log(ctx.work_dir, log_stage, exit_line)
        return proc.returncode == 0, "\n".join(stdout_lines), "\n".join(stderr_lines)

    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()
        msg = f"TIMEOUT after {timeout}s"
        ctx.log.write(msg, stream="system")
        for log_stage in (stage, "deployment"):
            _append_to_stage_log(ctx.work_dir, log_stage, f"\n[TIMEOUT] {msg}\n")
        return False, "", msg

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        ctx.log.write(f"ERROR: {msg}", stream="system")
        for log_stage in (stage, "deployment"):
            _append_to_stage_log(ctx.work_dir, log_stage, f"\n[ERROR] {msg}\n")
        return False, "", msg


def _terraform(*args) -> list[str]:
    tf = shutil.which("terraform") or "terraform"
    return [tf, *args]


# ── Stage: INIT ───────────────────────────────────────────────────────────────

def stage_init(ctx: StageContext) -> StageResult:
    """terraform fmt + terraform init."""
    from executor.failure_classifier import classify_terraform_error
    ctx.log.write("=== STAGE: INIT ===", stream="system")

    _run(_terraform("fmt", "-recursive", ctx.work_dir), ctx, "init")

    ok, _, stderr = _run(_terraform("init", "-input=false", "-no-color"), ctx, "init")
    if not ok:
        failure = classify_terraform_error(stderr, "init")
        return StageResult(
            success=False,
            error="terraform init failed",
            data={"failure": failure},
        )
    return StageResult(success=True)


# ── Stage: PLAN ───────────────────────────────────────────────────────────────

def stage_plan(ctx: StageContext) -> StageResult:
    """terraform plan -out=tfplan -json → parse summary."""
    from executor.failure_classifier import classify_terraform_error
    ctx.log.write("=== STAGE: PLAN ===", stream="system")

    plan_file = os.path.join(ctx.work_dir, "tfplan")
    ok, _, plan_stderr = _run(
        _terraform("plan", "-input=false", "-no-color", f"-out={plan_file}"),
        ctx, "plan",
    )
    if not ok:
        failure = classify_terraform_error(plan_stderr, "plan")
        return StageResult(
            success=False,
            error="terraform plan failed",
            data={"failure": failure},
        )

    # Parse plan summary via terraform show -json
    ok_show, show_json, _ = _run(
        _terraform("show", "-json", plan_file),
        ctx, "plan",
        capture_stdout=True,
    )
    summary: dict = {}
    if ok_show and show_json:
        try:
            plan_data = json.loads(show_json)
            changes = plan_data.get("resource_changes", [])
            add = sum(1 for c in changes if "create" in c.get("change", {}).get("actions", []))
            update = sum(1 for c in changes if "update" in c.get("change", {}).get("actions", []))
            delete = sum(1 for c in changes if "delete" in c.get("change", {}).get("actions", []))
            summary = {"add": add, "update": update, "delete": delete, "total": len(changes)}
            ctx.log.write(f"Plan: {add} to add, {update} to change, {delete} to destroy.", stream="system")
        except (json.JSONDecodeError, KeyError):
            pass

    return StageResult(success=True, data={"plan_summary": summary, "plan_file": plan_file})


# ── Stage: APPLY ──────────────────────────────────────────────────────────────

def stage_apply(ctx: StageContext, plan_file: str) -> StageResult:
    """terraform apply -auto-approve <planfile>."""
    from executor.failure_classifier import classify_terraform_error
    ctx.log.write("=== STAGE: APPLY ===", stream="system")

    ok, _, apply_stderr = _run(
        _terraform("apply", "-input=false", "-no-color", "-auto-approve", plan_file),
        ctx, "apply",
        timeout=1800,
    )
    if not ok:
        failure = classify_terraform_error(apply_stderr, "apply")
        return StageResult(
            success=False,
            error="terraform apply failed",
            data={"failure": failure},
        )

    # Capture outputs
    ok_out, out_json, _ = _run(
        _terraform("output", "-json"),
        ctx, "apply",
        capture_stdout=True,
    )
    outputs: dict = {}
    if ok_out and out_json:
        try:
            raw = json.loads(out_json)
            # Extract only "value" fields — never log sensitive outputs
            outputs = {k: v.get("value") for k, v in raw.items() if "sensitive" not in v or not v["sensitive"]}
        except (json.JSONDecodeError, AttributeError):
            pass

    return StageResult(success=True, data={"apply_outputs": outputs})


# ── Stage: DEPLOY_SH ─────────────────────────────────────────────────────────

def stage_deploy_sh(ctx: StageContext, apply_outputs: dict) -> StageResult:
    """Execute deploy.sh if present — runs data migration steps (S3→Blob, PostgreSQL).

    Non-blocking: if deploy.sh is absent or exits non-zero for a data step,
    the overall deployment is still marked successful (the IaC apply already
    completed). Failures are recorded as warnings in the result data.
    """
    import stat as _stat

    ctx.log.write("=== STAGE: DEPLOY_SH ===", stream="system")

    deploy_sh = Path(ctx.work_dir) / "deploy.sh"
    if not deploy_sh.exists():
        # Agent 03 writes deploy.sh to get_output_dir(<migration_id>), which is
        # output/<uuid>/ — different from work_dir (output/runs/<uuid>/). Try there.
        migration_id = getattr(ctx.manifest, "migration_id", None)
        if migration_id:
            from core.paths import get_output_dir
            deploy_sh = get_output_dir(migration_id) / "deploy.sh"
    if not deploy_sh.exists():
        ctx.log.write("deploy.sh not found — skipping data migration stage.", stream="system")
        return StageResult(success=True, data={"deploy_sh_skipped": True, "apply_outputs": apply_outputs})

    # Ensure executable on POSIX
    try:
        current_mode = deploy_sh.stat().st_mode
        deploy_sh.chmod(current_mode | _stat.S_IXUSR | _stat.S_IXGRP)
    except Exception:
        pass

    ctx.log.write(f"Executing deploy.sh: {deploy_sh}", stream="system")

    # Inject terraform outputs as env vars so deploy.sh can use them even if
    # terraform output commands fail (e.g. no backend configured in runner env).
    extra_env = {}
    for k, v in (apply_outputs or {}).items():
        if v is not None:
            extra_env[k.upper()] = str(v)

    deploy_env = {**ctx.env, **extra_env}

    # Use bash explicitly (deploy.sh may not be executable on Windows runners)
    import shutil as _shutil
    bash = _shutil.which("bash") or "bash"
    ok, _, deploy_stderr = _run(
        [bash, str(deploy_sh)],
        StageContext(
            job_id=ctx.job_id,
            work_dir=ctx.work_dir,
            env=deploy_env,
            log=ctx.log,
            manifest=ctx.manifest,
        ),
        "deploy_sh",
        timeout=1800,
    )

    if not ok:
        # Data migration failure is a WARNING, not a hard failure — infrastructure
        # was already deployed successfully by terraform apply.
        ctx.log.write(
            "⚠️ deploy.sh exited with non-zero status — data migration may be incomplete. "
            "Infrastructure deployment is still successful.",
            stream="system",
        )
        return StageResult(
            success=True,
            data={
                "deploy_sh_warning": deploy_stderr[-500:] if deploy_stderr else "deploy.sh failed",
                "apply_outputs": apply_outputs,
            },
        )

    ctx.log.write("✅ deploy.sh completed — data migration done.", stream="system")
    return StageResult(success=True, data={"deploy_sh_ok": True, "apply_outputs": apply_outputs})


# ── Stage: VERIFY ─────────────────────────────────────────────────────────────

def stage_verify(ctx: StageContext, apply_outputs: dict) -> StageResult:
    """Post-apply verification: terraform show to confirm state is non-empty."""
    ctx.log.write("=== STAGE: VERIFY ===", stream="system")

    ok, show_text, _ = _run(
        _terraform("show", "-no-color"),
        ctx, "verify",
        capture_stdout=True,
    )
    if not ok:
        return StageResult(success=False, error="terraform show failed after apply")

    resource_count_match = re.search(r"(\d+) resource", show_text)
    resource_count = int(resource_count_match.group(1)) if resource_count_match else 0
    ctx.log.write(f"Verification: {resource_count} resource(s) in state.", stream="system")

    return StageResult(success=True, data={"resource_count": resource_count, "apply_outputs": apply_outputs})
