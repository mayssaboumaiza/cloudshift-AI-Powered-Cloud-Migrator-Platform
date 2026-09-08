"""
agent_03.py — AI Deployment Preparation Agent (ReAct)

Phase 2 redesign: Agent 03 is now an ORCHESTRATOR ONLY.
- Checks deployment readiness (quotas, provider API availability)
- Generates deploy.sh via render_deploy_template (Jinja2)
- Generates .github/workflows/deploy.yml via generate_cicd_pipeline
- Builds a DeploymentManifest describing the job
- Returns the manifest in state — terraform execution happens in the runner process

Terraform init/plan/apply NEVER runs inside this agent or the FastAPI process.
The LangGraph enqueue_deploy node submits the manifest to the runner job queue.

SECURITY:
- No terraform subprocess calls in this file
- No credentials fetched or stored in agent memory
- deploy.sh uses env-var placeholders — runner injects real values at APPLY stage
"""
import json
import logging
import os
import re
import threading
from pathlib import Path

from dotenv import load_dotenv
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))

from langchain_core.messages import HumanMessage
import httpx
from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent

from agents.pipeline_state import MigrationState
from agents.deployer.agent_utilities import extract_json
from configuration.settings import settings
from agents.iac_generator.reporting_tools import gen_deploy_report, gen_rollback_report
from agents.iac_generator.cloud_integration import (
    check_deployment_readiness,
    get_deployment_context,
    render_deploy_template,
)
from agents.iac_generator.cicd_tools import generate_cicd_pipeline
# execute_terraform_apply intentionally excluded — terraform runs in the runner process.
# write_terraform_file intentionally excluded — Agent 03 must not modify .tf files.

logger = logging.getLogger("Agent03")

_AZURE_TIMEOUT = httpx.Timeout(timeout=float(os.getenv("AZURE_REQUEST_TIMEOUT", "180")), connect=15.0)

def _get_output_dir_03() -> Path:
    """Re-read MIGRATION_OUTPUT_DIR on every call so Agent 03 uses the per-migration
    path set by Agent 02 (generator.py overrides the env var before its ReAct loop)."""
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    if _out_env and os.path.isabs(_out_env):
        return Path(_out_env)
    return Path(_PROJECT_ROOT) / (_out_env or os.path.join("output", "migrated_app"))

_OUTPUT_DIR = _get_output_dir_03()

# ─────────────────────────────────────────────────────────────────────────────
# LLM singleton (thread-safe — shared across parallel migrations)
# ─────────────────────────────────────────────────────────────────────────────

_llm_03 = None
_llm_03_lock = threading.Lock()


def _get_llm_03() -> AzureChatOpenAI:
    global _llm_03
    with _llm_03_lock:
        if _llm_03 is None:
            # Deployment name read from .env (AZURE_MODEL_03 → AZURE_MODEL → settings default = gpt-4.1)
            deployment = os.getenv("AZURE_MODEL_03") or os.getenv("AZURE_MODEL") or settings.AZURE_MODEL
            logger.info(f"Agent03: deployment={deployment}")
            _llm_03 = AzureChatOpenAI(
                azure_endpoint=os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT", ""),
                api_key=os.getenv("AZURE_AI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY", ""),
                azure_deployment=deployment,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION") or settings.AZURE_OPENAI_API_VERSION,
                temperature=0,
                max_tokens=4096,
                timeout=_AZURE_TIMEOUT,
            )
    return _llm_03


# ─────────────────────────────────────────────────────────────────────────────
# System prompt — 3-step workflow (no terraform execution)
# ─────────────────────────────────────────────────────────────────────────────

AGENT_03_SYSTEM = """You are an expert DevOps engineer responsible for deployment preparation.
You are a PREPARATION AGENT ONLY — you do NOT execute terraform or modify .tf files.
Terraform execution is handled by a separate runner process after you finish.

## Your 3-step workflow

Execute these steps IN ORDER.

### STEP 1 — Verify readiness
Call `check_deployment_readiness(provider, region, resources, migration_id)`.
- If ready=false: note the blocking checks and CONTINUE to STEP 2 and STEP 3 anyway.
  User needs the deploy.sh and CI/CD pipeline regardless.
  Set deployment_ready=false in your final JSON.
- If ready=true: proceed normally. Set deployment_ready=true in final JSON.

### STEP 2 — Render deploy script
Call `render_deploy_template(cloud_provider, region, resources_csv)`.
The tool renders the Jinja2 template AND writes deploy.sh to disk automatically.
NEVER write the script content yourself — always use render_deploy_template.

### STEP 3 — Generate CI/CD pipeline
Call `generate_cicd_pipeline(cloud_provider, region, "github_actions")`.
The tool writes deploy.yml automatically.

### STEP 4 — Generate deployment report
Call `gen_deploy_report(status, resources, errors, urls)` with the result.

## Output format (FINAL ANSWER)
Return EXACTLY this JSON — report_type MUST be "deploy" or "rollback":

```json
{
  "deployment_ready": true,
  "deploy_script_path": "output/migrated_app/deploy.sh",
  "cicd_pipeline_path": "output/migrated_app/deploy.yml",
  "warnings": [],
  "report_type": "deploy",
  "report": "# Deployment Preparation Report markdown..."
}
```

Note: terraform_steps_executed is NOT included — terraform runs in the runner process after you finish.

## Security rules
- NEVER include credentials, access keys, or secret tokens in any generated file.
- Credentials must come from environment variables only (the runner injects them at runtime).
- NEVER modify .tf files.
"""

AGENT_03_SYSTEM_NO_CREDS = """You are an expert DevOps engineer.
Credentials validation FAILED at form submission. Generate ONLY a rollback report.

Call `gen_rollback_report(destroyed=[], orphaned={resources}, costs={})` immediately.
Return JSON with report_type="rollback".
"""


# ─────────────────────────────────────────────────────────────────────────────
# Lazy agent singleton
# ─────────────────────────────────────────────────────────────────────────────

_agent_03_tools = [
    check_deployment_readiness,
    get_deployment_context,
    render_deploy_template,
    generate_cicd_pipeline,
    gen_deploy_report,
    gen_rollback_report,
    # execute_terraform_apply removed — terraform runs in the runner process
    # write_terraform_file excluded — Agent 03 must not modify .tf files
]

_agent_03 = None
_agent_03_lock = threading.Lock()


def _get_agent_03():
    global _agent_03
    with _agent_03_lock:
        if _agent_03 is None:
            _agent_03 = create_react_agent(
                _get_llm_03(), _agent_03_tools, prompt=AGENT_03_SYSTEM
            )
    return _agent_03


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic post-validation of agent 03 output
# ─────────────────────────────────────────────────────────────────────────────

_CREDENTIAL_PATTERNS = re.compile(
    r"(AKIA[0-9A-Z]{16}|['\"](?:password|secret|token|key)\s*[:=]\s*['\"][^'\"]{8,}['\"])",
    re.IGNORECASE,
)

_VALID_REPORT_TYPES = {"deploy", "rollback"}


def _validate_agent_03_output(summary: dict | None) -> list[str]:
    """Deterministic checks on Agent 03's final output.

    Returns a list of warning strings (empty = all OK).
    """
    issues: list[str] = []

    if not summary:
        issues.append("Agent03 returned no parseable JSON summary.")
        return issues

    report_type = summary.get("report_type", "")
    if report_type not in _VALID_REPORT_TYPES:
        issues.append(
            f"report_type '{report_type}' is not in allowed values {_VALID_REPORT_TYPES}. "
            f"Defaulting to 'deploy'."
        )

    deploy_sh = _get_output_dir_03() / "deploy.sh"
    deployment_blocked = not summary.get("deployment_ready", True)
    if not deploy_sh.exists() and not deployment_blocked:
        issues.append("deploy.sh was not written to output/migrated_app/.")
    elif deploy_sh.exists():
        content = deploy_sh.read_text(encoding="utf-8", errors="ignore")
        if "set -e" not in content:
            issues.append("deploy.sh is missing 'set -e' — script may silently ignore errors.")
        match = _CREDENTIAL_PATTERNS.search(content)
        if match:
            issues.append(
                f"deploy.sh may contain hardcoded credentials near: "
                f"'{match.group()[:60]}...'"
            )

    report = summary.get("report", "")
    if not report or len(report.strip()) < 50:
        issues.append("Deployment report is empty or too short.")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# ReAct loop with feedback injection (max 3 iterations)
# ─────────────────────────────────────────────────────────────────────────────

def _run_agent_03_with_feedback(
    input_msg: str,
    system_prompt: str | None = None,
) -> tuple[dict | None, list]:
    if system_prompt and system_prompt != AGENT_03_SYSTEM:
        agent = create_react_agent(_get_llm_03(), _agent_03_tools, prompt=system_prompt)
    else:
        agent = _get_agent_03()

    initial_messages: list = [HumanMessage(content=input_msg)]
    messages: list = list(initial_messages)
    last_messages: list = list(initial_messages)
    max_iterations = 3

    for iteration in range(max_iterations):
        logger.info(f"Agent03 iteration {iteration + 1}/{max_iterations}")

        try:
            result = agent.invoke(
                {"messages": messages},
                config={"recursion_limit": 60},
            )
            last_messages = result.get("messages", []) if isinstance(result, dict) else []
            if not last_messages:
                # Diagnostic: log the raw result so we can tell WHY the LLM produced
                # nothing (empty completion / content filter / bad state) instead of
                # silently falling back to the Jinja2 template every single run.
                logger.warning(
                    "Agent03 iteration %d: invoke returned 0 messages — result type=%s keys=%s repr=%.500s",
                    iteration + 1,
                    type(result).__name__,
                    list(result.keys()) if isinstance(result, dict) else "n/a",
                    repr(result),
                )
                messages = list(initial_messages) + [HumanMessage(content=(
                    "No output received. Restart from check_deployment_readiness "
                    "and follow the 3-step workflow."
                ))]
                continue

            last_msg = last_messages[-1]
            content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

            summary = extract_json(content)
            if summary is None:
                feedback = (
                    "Your previous response could not be parsed as JSON. "
                    "You MUST end your response with a JSON block like:\n"
                    "```json\n{\"deployment_ready\": true, \"report_type\": \"deploy\", "
                    "\"warnings\": [], "
                    "\"deploy_script_path\": \"output/migrated_app/deploy.sh\", "
                    "\"report\": \"...\"}\n```\n"
                    "Try again."
                )
                logger.warning(f"Agent03 iteration {iteration + 1}: JSON parse failed — injecting feedback")
                messages = list(initial_messages) + [HumanMessage(content=feedback)]
                continue

            logger.info(f"Agent03 iteration {iteration + 1}: success")
            return summary, last_messages

        except Exception as e:
            # Surface the underlying Azure OpenAI error (status code / body) — a 400
            # quota error or content filter looks identical to a generic failure
            # without this. status_code / response live on most azure/openai errors.
            status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
            body = getattr(getattr(e, "response", None), "text", "")
            logger.error(
                "Agent03 iteration %d exception: %s | status=%s | body=%.300s",
                iteration + 1, e, status, body,
                exc_info=True,
            )
            if iteration < max_iterations - 1:
                messages = list(initial_messages) + [HumanMessage(content=(
                    f"An error occurred: {str(e)[:200]}. "
                    f"Please retry from check_deployment_readiness."
                ))]
            else:
                return None, last_messages

    logger.error("Agent03: max iterations reached without valid JSON")
    return None, last_messages


def _save_agent_03_debug(messages: list, metadata: dict) -> None:
    try:
        _get_output_dir_03().mkdir(parents=True, exist_ok=True)
        debug_path = _get_output_dir_03() / "agent_03_messages_debug.json"
        serialized: list[dict] = []
        for m in messages:
            msg_type = getattr(m, "type", None) or m.__class__.__name__.lower().replace("message", "")
            content = getattr(m, "content", "")
            if isinstance(content, list):
                content = "\n".join(str(p) for p in content)
            entry = {"type": msg_type, "content": str(content)[:6000]}
            tool_calls = getattr(m, "tool_calls", None) or []
            if tool_calls:
                entry["tool_calls"] = [
                    {
                        "name": tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", ""),
                        "args": tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {}),
                    }
                    for tc in tool_calls
                ]
            tool_call_id = getattr(m, "tool_call_id", None)
            if tool_call_id:
                entry["tool_call_id"] = tool_call_id
            serialized.append(entry)
        debug_path.write_text(
            json.dumps({"metadata": metadata, "messages": serialized}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"_save_agent_03_debug: wrote {debug_path} ({len(messages)} messages)")
    except Exception as e:
        logger.warning(f"_save_agent_03_debug: failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Fallback deploy.sh generator — runs after the LLM loop, only if file absent
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_deploy_sh(
    cloud_provider: str,
    region: str,
    resources: list[str],
    migration_id: str,
) -> None:
    """Write deploy.sh via render_deploy_template if it was not produced by Agent 03 LLM.

    This guarantees the health check always finds the file and the pipeline
    does not show a spurious '⚠️ deploy.sh not found' warning when the LLM
    call timed out or returned no messages.
    """
    from agents.iac_generator.cloud_integration import render_deploy_template
    out_dir = _get_output_dir_03()
    deploy_sh = out_dir / "deploy.sh"
    if deploy_sh.exists():
        return  # LLM already wrote it — nothing to do

    logger.warning(
        "Agent03: deploy.sh missing after LLM loop — generating via render_deploy_template fallback"
    )
    try:
        resources_csv = ",".join(r for r in resources if r)
        render_deploy_template.invoke({
            "cloud_provider": cloud_provider or "azure",
            "region": region or "westeurope",
            "resources_csv": resources_csv,
        })
        if deploy_sh.exists():
            logger.info("Agent03 fallback: deploy.sh written to %s", deploy_sh)
        else:
            logger.error("Agent03 fallback: render_deploy_template ran but deploy.sh still missing")
    except Exception as exc:
        logger.error("Agent03 fallback: render_deploy_template raised %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Manifest builder — converts MigrationState → DeploymentManifest
# ─────────────────────────────────────────────────────────────────────────────

def _build_manifest(state: MigrationState, work_dir: str) -> dict:
    """Build a DeploymentManifest dict from the current pipeline state.

    secret_refs holds vault_paths (not plaintext credentials).
    The runner fetches actual credentials JIT from SecretBroker at APPLY stage.
    """
    from executor.manifest import DeploymentManifest, CloudProvider

    migration_id = state.get("migration_id", "")
    thread_id = state.get("thread_id", migration_id)
    target_cloud = (state.get("target_cloud") or "azure").lower().replace("azurerm", "azure")
    arch = state.get("architecture_specs") or {}
    region = (
        arch.get("region")
        or arch.get("target_region")
        or state.get("target_region")
        or {"azure": "westeurope", "aws": "eu-west-1", "gcp": "europe-west1"}.get(target_cloud, "westeurope")
    )

    # Map provider → vault path (uses canonical role names from SecretBroker)
    from services.credentials.broker import get_secret_broker
    broker = get_secret_broker()
    provider_role_map = {
        "azure": "azure", "aws": "aws", "gcp": "gcp",
    }
    role = provider_role_map.get(target_cloud, target_cloud)
    secret_refs: dict[str, str] = {}
    if migration_id:
        secret_refs[role] = broker.migration_path(migration_id, role)
        # Always include github_token if available
        secret_refs["github_token"] = broker.migration_path(migration_id, "github_token")

    tf_files = [
        str(p.relative_to(work_dir))
        for p in Path(work_dir).glob("*.tf")
    ] if Path(work_dir).exists() else []

    manifest = DeploymentManifest(
        migration_id=migration_id,
        thread_id=thread_id,
        work_dir=work_dir,
        tf_files=tf_files,
        provider=target_cloud,
        target_region=region,
        secret_refs=secret_refs,
        auto_approve=True,
        budget_usd=state.get("monthly_budget_usd"),
        deploy_script="deploy.sh" if (Path(work_dir) / "deploy.sh").exists() else None,
        cicd_yaml="deploy.yml" if (Path(work_dir) / "deploy.yml").exists() else None,
    )
    return manifest.model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# State node function (called by graph.py)
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_03(state: MigrationState) -> dict:
    """LangGraph node: deployment preparation.

    Generates deploy.sh + deploy.yml via LLM, then builds a DeploymentManifest
    describing the job for the runner. Returns deployment_manifest in state.
    The enqueue_deploy node (graph.py) submits the manifest to the job queue.
    """
    target_cloud = state.get("target_cloud", "")
    migration_id = state.get("migration_id", "")
    migration_plan = state.get("migration_plan", {})
    iac_validation = (state.get("artifacts") or {}).get("iac_validation", {})
    generated_files = state.get("generated_code_files", [])
    arch = state.get("architecture_specs") or {}
    region = (
        arch.get("region")
        or arch.get("target_region")
        or state.get("target_region")
        or {
            "azure": "westeurope", "azurerm": "westeurope",
            "aws": "eu-west-1", "gcp": "europe-west1", "google": "europe-west1",
        }.get((target_cloud or "azure").lower(), "westeurope")
    )
    project_id = arch.get("project_id", "") or state.get("project_id", "")
    creds_pre_validated = state.get("credentials_pre_validated", False)

    resources = [
        r.get("target_service") or r.get("target_equivalent") or r.get("resource_name", "")
        for r in migration_plan.get("resources", [])
        if r.get("strategy") not in ("RETIRE", "RETAIN")
    ]
    resources = [r for r in resources if r]

    # Set MIGRATION_OUTPUT_DIR so render_deploy_template and generate_cicd_pipeline
    # write deploy.sh / deploy.yml to the correct per-migration output directory.
    # Agent 02 sets this env var before its ReAct loop, but Agent 03 runs later in a
    # different call — without this re-set the tools fall back to the module-level
    # _OUTPUT_DIR resolved at import time, which may point to a different migration.
    if migration_id:
        from core.paths import get_output_dir
        migration_out = get_output_dir(migration_id)
        migration_out.mkdir(parents=True, exist_ok=True)
        os.environ["MIGRATION_OUTPUT_DIR"] = str(migration_out)
        logger.info("Agent03: MIGRATION_OUTPUT_DIR set to %s", migration_out)

    system_prompt = AGENT_03_SYSTEM

    if creds_pre_validated:
        creds_note = (
            f"IMPORTANT: Credentials for '{target_cloud}' were already validated at "
            f"form-submission time. Terraform execution will happen in the runner process. "
            f"Your role is ONLY to generate deploy.sh, deploy.yml, and the readiness report."
        )
    else:
        creds_note = (
            f"WARNING: Credentials for '{target_cloud}' were NOT pre-validated. "
            f"Generate a best-effort deploy.sh — user will provide credentials at runtime."
        )

    input_msg = (
        f"Prepare the deployment for the migrated application (3-step workflow).\n\n"
        f"## Context\n"
        f"target_cloud:          {target_cloud}\n"
        f"migration_id:          {migration_id}\n"
        f"region:                {region}\n"
        f"project_id:            {project_id or '(not applicable)'}\n"
        f"resources_to_deploy:   {json.dumps(resources)}\n"
        f"iac_validation_passed: {state.get('iac_validation_success', False)}\n"
        f"generated_tf_files:    {json.dumps(generated_files)}\n"
        f"{creds_note}\n\n"
        f"## IaC validation results\n"
        f"{json.dumps(iac_validation, indent=2)}\n\n"
        f"## Your instructions (execute IN ORDER)\n"
        f"STEP 1 — Call check_deployment_readiness(provider='{target_cloud}', "
        f"region='{region}', resources={json.dumps(resources)}, "
        f"migration_id='{migration_id}').\n\n"
        f"STEP 2 — Call render_deploy_template(cloud_provider='{target_cloud}', "
        f"region='{region}', resources_csv='{','.join(resources)}'). "
        f"The tool writes deploy.sh to disk automatically.\n\n"
        f"STEP 3 — Call generate_cicd_pipeline(cloud_provider='{target_cloud}', "
        f"region='{region}', pipeline_type='github_actions').\n\n"
        f"STEP 4 — Call gen_deploy_report with the final result.\n\n"
        f"Return the final JSON summary. Do NOT include terraform_steps_executed — "
        f"terraform runs in the runner process after you finish."
    )

    summary, message_trace = _run_agent_03_with_feedback(input_msg, system_prompt=system_prompt)

    if os.getenv("DEBUG_AGENT", "").lower() in ("1", "true", "yes"):
        _save_agent_03_debug(
            message_trace,
            metadata={
                "target_cloud": target_cloud,
                "region": region,
                "creds_pre_validated": creds_pre_validated,
                "summary_present": summary is not None,
            },
        )

    # Fallback: if Agent 03 LLM failed to write deploy.sh, generate it deterministically
    # so the health check always finds the file and the pipeline stays green.
    _ensure_deploy_sh(target_cloud, region, resources, migration_id)

    post_issues = _validate_agent_03_output(summary)
    for issue in post_issues:
        logger.warning(f"Agent03 post-validation: {issue}")

    raw_report_type = (summary or {}).get("report_type", "deploy")
    report_type = raw_report_type if raw_report_type in _VALID_REPORT_TYPES else "deploy"
    if raw_report_type not in _VALID_REPORT_TYPES:
        logger.warning(f"Agent03: invalid report_type '{raw_report_type}' → defaulting to 'deploy'")

    report = (summary or {}).get("report", "")
    report_key = "deploy_report" if report_type == "deploy" else "rollback_report"

    # deployment_ready is based on .tf files existing — NOT on Agent 03's LLM judgment.
    # Agent 03 may block on Checkov warnings or az-CLI absence; the TerraformRunner
    # handles actual execution with proper credentials. Security warnings are preserved
    # in the report but must not block automated deployment.
    work_dir = str(_get_output_dir_03().resolve())
    tf_files = list(Path(work_dir).glob("*.tf")) if Path(work_dir).exists() else []
    deployment_ready = len(tf_files) > 0
    if not deployment_ready:
        logger.warning("Agent03: no .tf files found in %s — deployment blocked", work_dir)
    else:
        agent_ready = bool((summary or {}).get("deployment_ready", False))
        if not agent_ready:
            logger.info(
                "Agent03: LLM reported deployment_ready=False but %d .tf files exist "
                "— proceeding with deployment (security warnings preserved in report)",
                len(tf_files),
            )

    deployment_manifest = _build_manifest(state, work_dir)

    return {
        "deployment_status": "queued" if deployment_ready else "blocked",
        "deployment_manifest": deployment_manifest,
        "artifacts": {
            **(state.get("artifacts") or {}),
            report_key: report,
            "deployment_summary": summary,
            "agent03_post_validation_issues": post_issues,
            "cicd_pipeline_path": (summary or {}).get("cicd_pipeline_path", ""),
        },
    }
