"""
generator.py — Agent 02 IaC generation: slim orchestration entry point.

Responsibilities:
  - Build the RAG context from the Terraform Knowledge Graph (pgvector).
  - Run Phase 1: migrate Python SDK files (deterministic + LLM-assisted).
  - Run Phase 2: hand-rolled ReAct loop that drives GPT-4o to write .tf files.
  - Run deterministic post-passes after generation (7 fixer functions).
  - Expose run_agent_02() and run_agent_02_fix() as LangGraph node functions.

All heavy logic is in dedicated modules:
  llm_config.py           — AzureChatOpenAI factory + _AZURE_TIMEOUT
  iac_system_prompts.py   — AGENT_02_SYSTEM, AGENT_02_FIX_SYSTEM, PYTHON_MIGRATION_SYSTEM
  iac_fixers.py           — error classifier + 7 deterministic post-pass fixers
  python_migrator.py      — .env / requirements.txt / Python SDK migration
  react_loop.py           — hand-rolled ReAct loop + debug dump utilities
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))

from langchain_core.messages import HumanMessage, SystemMessage

from agents.pipeline_state import MigrationState
from agents.iac_generator.security_policy_engine import SecurityPolicyEngine as _SPE
from agents.iac_generator.hcl_merge import write_terraform_file, _get_output_dir
from agents.iac_generator.react_tools import (
    validate_terraform_block,
    get_rag_context_for_resource,
    read_generated_files,
)
from agents.iac_generator.execution_graph import build_iac_guidance
from agents.iac_generator.llm_config import _get_llm_02
from agents.iac_generator.iac_system_prompts import (
    AGENT_02_SYSTEM,
    AGENT_02_FIX_SYSTEM,
    AGENT_02_SELF_REVIEW_SYSTEM,
    PYTHON_MIGRATION_SYSTEM,
)
from agents.iac_generator.iac_fixers import (
    _classify_tf_errors,
    _read_generated_tf_files,
    _fix_tf_cross_references,
    _fix_tf_variables,
    _add_missing_variable_declarations,
    _prune_orphan_required_variables,
    _normalize_provider_versions,
    _fix_deprecated_azurerm_attrs,
    _fix_postgresql_bsku_ha,
    _fix_postgresql_timeouts,
    _inject_missing_security_attrs,
    _inject_azure_network,
    _disable_role_assignments_by_default,
    _ensure_health_check_outputs,
    _ensure_resource_group,
    _ensure_required_business_attrs,
    _fix_resource_group_references,
    _fix_region_availability,
    _fix_globally_unique_names,
    _fix_provider_config,
    _fix_corrupted_string_literals,
    _fix_orphan_preamble,
    _fix_stray_chars_after_braces,
    _fix_glued_hcl_arguments,
    _fix_storage_sas_policy_nesting,
    _fix_cognitive_network_acls,
    _fix_compliance_standards,
    _fix_ha_requirement,
    _fix_network_isolation,
    _fix_role_assignment_permissions,
    _deduplicate_variable_blocks,
)
from agents.iac_generator.python_migrator import (
    _migrate_env_file,
    _migrate_requirements_txt,
    _migrate_python_sdk_with_llm,
    _build_comprehensive_migration_context,
)
from agents.iac_generator.react_loop import (
    _run_iac_react_loop,
    _save_debug_messages,
    _count_tool_calls,
    _execute_tool_call,
)

logger = logging.getLogger("Agent02")

# GraphRAG — single source of truth for Terraform argument schemas.
# Uses RAGProvider protocol for testability (inject a FakeRAG in tests).
from rag.graph_rag import TerraformGraphRAG as _GraphRAG  # noqa: E402
from rag.protocol import get_default_rag_provider as _get_rag_provider  # noqa: E402

# Maximum RAG context size injected into the prompt.
# Azure GPT-4o has a 128K context window; we reserve room for the system
# prompt, resources table, and LLM reasoning. Original limit was 60K after
# observing silent {} returns on some deployments; raised to 90K after adding
# intelligent priority-based chunking that ensures the most critical resources
# (REFACTOR > REPLATFORM > REHOST) always get full context within the budget.
_MAX_RAG_CONTEXT_CHARS = 90_000

# ── Tools list for the IaC generation ReAct loop ──────────────────────────────
_agent_02_tools = [
    write_terraform_file,
    validate_terraform_block,
    get_rag_context_for_resource,
    read_generated_files,
]


def extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a text string."""
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"```(?:json)?\s*(\{[^`]{0,100000}?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    m = re.search(r"\{.{0,100000}\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _format_traversal_block(traversal: dict) -> str:
    """Format graph traversal as a compact dependency reference for the LLM prompt.

    Converts the resource_traversals dict into a human-readable section that
    tells the LLM which companion/dependency resources it MUST generate alongside
    each primary resource, AND which arguments are required on each dependency.

    Uses Neo4j Cypher `required_args` field (collected via HAS_ARGUMENT edges)
    so the LLM knows mandatory fields on dependency resources without needing
    to re-query RAG — directly implements the GR-Ref + argument signal from
    Nekrasov et al. 2025.
    """
    resource_traversals: dict = traversal.get("resource_traversals", {})
    if not resource_traversals:
        return ""

    lines = ["=== STRUCTURAL DEPENDENCIES (Knowledge Graph — Neo4j) ==="]
    any_deps = False

    for resource, data in resource_traversals.items():
        companions = data.get("companions_fetched", [])
        multi_hop = data.get("multi_hop_nodes", [])

        # Only depth-1 hard constraints to avoid GR-Ref context overload
        depth1_deps = [
            n for n in multi_hop
            if n.get("depth") == 1 and n.get("relation") in ("COMPANION", "DEPENDS_ON")
        ]

        # Build a map: dep_resource → required_args (from Neo4j HAS_ARGUMENT edges)
        dep_required_args: dict[str, list[str]] = {}
        for n in depth1_deps:
            req = [a for a in (n.get("required_args") or []) if a]
            if req:
                dep_required_args[n["resource"]] = req

        dep_names = list(dict.fromkeys(companions + [n["resource"] for n in depth1_deps]))

        # REFERENCES are NOT hard constraints (a reference does not mean the
        # referenced resource must be created). They are surfaced as CONTEXT so
        # the LLM knows how to wire attributes IF the resource is in the plan —
        # distinct from "MUST generate" above to avoid over-generation.
        referenced = [
            n["resource"] for n in multi_hop
            if n.get("depth") == 1 and n.get("relation") == "REFERENCES"
            and n["resource"] not in dep_names
        ]

        if dep_names or referenced:
            any_deps = True
            lines.append(f"  {resource}:")
            for dep in dep_names:
                req_args = dep_required_args.get(dep, [])
                if req_args:
                    # Neo4j provided the required args — surface them explicitly
                    lines.append(f"    → MUST generate: {dep}  [required args: {', '.join(req_args)}]")
                else:
                    lines.append(f"    → MUST generate alongside: {dep}")
            for ref in referenced:
                lines.append(f"    → may reference (only if in plan): {ref}")

    if not any_deps:
        return ""
    lines.append("=== END DEPENDENCIES ===")
    return "\n".join(lines)


def _get_rag_context(migration_plan: dict) -> tuple[str, str, dict, dict]:
    """Return (full_context_block, dependency_block, traversal_data, per_resource_contexts).

    Falls back to empty context if GraphRAG is unavailable — Agent 02 will still
    generate Terraform files using its system prompt and LLM knowledge.
    """
    rag      = _GraphRAG.get_instance()
    contexts = rag.get_contexts_for_plan(migration_plan)
    traversal = rag.get_traversal_for_plan(migration_plan)

    if not contexts:
        logger.warning(
            "GraphRAG returned no contexts for the migration plan — "
            "continuing without RAG context. Run rag/builder.py to populate pgvector "
            "and ensure plan terraform_resource names match indexed resources."
        )
        return "", "", {}, {}

    per_resource: dict[str, str] = {}

    # Collect all target resource types for cross-resource context
    all_target_resources = list(contexts.keys())

    for resource, ctx in contexts.items():
        resource_lines = [f"\n--- {resource} ---\n{ctx}"]

        # ── Signal 1: Argument-level nodes (pgvector — Nekrasov Graph RAG) ───────
        arg_nodes = rag.get_argument_context(
            resource,
            query_text=f"{resource} required arguments configuration",
            top_k=15,
        )
        if arg_nodes:
            req = [a for a in arg_nodes if a["is_required"]]
            opt = [a for a in arg_nodes if not a["is_required"]]
            if req:
                resource_lines.append("REQUIRED ARGUMENT NODES (from Knowledge Graph):")
                for a in req:
                    resource_lines.append(
                        f"  {a['arg_name']} [{a['arg_type'] or 'any'}]: {a['description'][:140]}"
                    )
            if opt:
                resource_lines.append("OPTIONAL ARGUMENT NODES (top matches):")
                for a in opt[:10]:
                    resource_lines.append(
                        f"  {a['arg_name']} [{a['arg_type'] or 'any'}]: {a['description'][:100]}"
                    )

        # ── Signal 2: Canonical patterns (graph_rag_query — Neo4j + pgvector) ────
        # Retrieves pre-validated HCL templates from tf_canonical_patterns that
        # match this resource in context. Provides structural examples the LLM
        # can follow directly rather than inferring from docs.
        try:
            provider = resource.split("_")[0] if "_" in resource else "azurerm"
            gq = rag.graph_rag_query(
                provider=provider,
                target_resource=resource,
                context_resources=[r for r in all_target_resources if r != resource],
                task_text=f"generate {resource} terraform configuration",
                top_k_chunks=0,       # docs already in ctx — skip to avoid duplication
                top_k_patterns=2,     # 2 canonical patterns max per resource
            )
            patterns = gq.get("canonical_patterns") or []
            if patterns:
                resource_lines.append("CANONICAL PATTERNS (pre-validated HCL templates from Knowledge Graph):")
                for p in patterns:
                    resource_lines.append(
                        f"  [{p['pattern_name']}] ({', '.join(p.get('resource_types', []))})"
                    )
                    if p.get("hcl_template"):
                        resource_lines.append(f"  Template:\n{p['hcl_template'][:600]}")
        except Exception as _e:
            logger.debug("graph_rag_query for %s: %s", resource, _e)

        per_resource[resource] = "\n".join(resource_lines)

    # ── Intelligent priority-based chunking ────────────────────────────────────
    # Budget allocation: REFACTOR (highest need) > REPLATFORM > REHOST.
    # Build a priority map from the migration plan so critical resources always
    # receive full docs even when total context exceeds _MAX_RAG_CONTEXT_CHARS.
    _STRATEGY_PRIORITY = {"REFACTOR": 0, "REPLATFORM": 1, "REHOST": 2, "REPURCHASE": 2}
    plan_services = {
        svc.get("target_service", svc.get("resource_name", "")):
            _STRATEGY_PRIORITY.get((svc.get("strategy_7r") or svc.get("strategy") or "").upper(), 2)
        for svc in (migration_plan or {}).get("resources", [])
    }

    def _resource_priority(r: str) -> int:
        return plan_services.get(r, 2)

    sorted_resources = sorted(per_resource.keys(), key=_resource_priority)
    full_lines = ["=== TERRAFORM DOCUMENTATION (GraphRAG) ==="]
    total_chars = len(full_lines[0])
    truncated_count = 0

    for resource in sorted_resources:
        block = per_resource[resource]
        if total_chars + len(block) <= _MAX_RAG_CONTEXT_CHARS:
            full_lines.append(block)
            total_chars += len(block)
        else:
            remaining = _MAX_RAG_CONTEXT_CHARS - total_chars - 200
            if remaining > 400:
                # Include a compact version: first 400 chars + required args only
                compact = block[:remaining]
                full_lines.append(compact + "\n  ...[truncated — use get_rag_context_for_resource]")
                total_chars = _MAX_RAG_CONTEXT_CHARS
            else:
                full_lines.append(
                    f"\n--- {resource} --- [omitted — context budget exhausted; use get_rag_context_for_resource]"
                )
            truncated_count += 1
            if total_chars >= _MAX_RAG_CONTEXT_CHARS:
                break

    full_lines.append("=== END DOCUMENTATION ===\n")
    full_context = "\n".join(full_lines)

    if truncated_count > 0:
        logger.warning(
            "GraphRAG: intelligent chunking active — %d resource(s) truncated/omitted "
            "(context_chars=%d/%d). High-priority (REFACTOR/REPLATFORM) resources got full docs.",
            truncated_count, len(full_context), _MAX_RAG_CONTEXT_CHARS,
        )

    dependency_block = _format_traversal_block(traversal)
    logger.info(
        f"GraphRAG: context built for {len(contexts)} resource(s) "
        f"(context_chars={len(full_context)}, deps_block={'yes' if dependency_block else 'empty'})"
    )
    return full_context, dependency_block, traversal, per_resource


def _count_resources_requiring_terraform(migration_plan: dict) -> int:
    """Count plan resources that need Terraform (excludes RETAIN and RETIRE)."""
    count = 0
    for resource in (migration_plan or {}).get("resources", []):
        if (resource.get("strategy") or "").upper() not in {"RETAIN", "RETIRE"}:
            count += 1
    return count


def _infer_category(target_service: str) -> str:
    """Derive the Terraform category filename from a resource type string."""
    t = (target_service or "").lower()
    if any(k in t for k in ["iam", "role", "identity", "policy", "service_account"]):
        return "iam"
    if any(k in t for k in ["storage", "bucket", "blob", "s3", "gcs"]):
        return "storage"
    if any(k in t for k in ["database", "cosmos", "dynamodb", "sql", "firestore", "spanner", "bigtable", "postgresql", "mysql"]):
        return "database"
    if any(k in t for k in ["function", "lambda", "cloud_run", "container_app", "virtual_machine", "instance", "compute"]):
        return "compute"
    if any(k in t for k in ["monitor", "log", "cloudwatch", "analytics", "insight", "alert", "metric"]):
        return "monitoring"
    if any(k in t for k in ["network", "vpc", "vnet", "subnet", "firewall", "security_group", "public_ip", "route_table", "internet_gateway"]):
        return "network"
    if any(k in t for k in ["pubsub", "sqs", "sns", "service_bus", "queue", "event"]):
        return "messaging"
    if any(k in t for k in ["kubernetes", "aks", "gke", "eks"]):
        return "compute"
    return "main"


SERVICE_TO_FILE: dict[str, str] = {
    "s3": "storage_client.py",           "cloud_storage": "storage_client.py",
    "blob_storage": "storage_client.py",
    "dynamodb": "database_client.py",    "firestore": "database_client.py",
    "cosmos_db": "database_client.py",
    "rds": "sql_client.py",              "cloud_sql": "sql_client.py",
    "azure_sql": "sql_client.py",
    "lambda": "serverless_handler.py",   "cloud_functions": "serverless_handler.py",
    "azure_functions": "serverless_handler.py",
    "cloud_run": "container_service.py", "container_apps": "container_service.py",
    "ec2": "compute_client.py",          "compute_engine": "compute_client.py",
    "sqs": "messaging_client.py",        "sns": "messaging_client.py",
    "pubsub": "messaging_client.py",     "service_bus": "messaging_client.py",
    "event_grid": "messaging_client.py",
    "bedrock": "llm_client.py",          "bedrock-runtime": "llm_client.py",
    "vertex_ai": "llm_client.py",        "azure_ai": "llm_client.py",
    "chromadb": "vector_store.py",       "pinecone": "vector_store.py",
    "opensearch": "search_client.py",    "vertex_ai_search": "search_client.py",
    "azure_search": "search_client.py",
    "iam": "iam_config.py",              "azure_ad": "iam_config.py",
}


def _run_fix_react_loop(input_msg: str, output_dir: "Path") -> list:
    """Hand-rolled ReAct loop for Agent02Fix — mirrors react_loop._run_iac_react_loop().

    create_react_agent silently returns {'messages': []} when called inside the outer
    LangGraph with a Postgres checkpointer (thread_id leakage to END). This bypass
    calls llm.bind_tools().invoke() directly, which is unaffected by LangGraph routing.
    """
    import time as _time
    from langchain_core.messages import AIMessage as _AIMessage

    MAX_STEPS        = 40
    MAX_RATE_RETRIES = 5
    NO_PROGRESS_LIMIT = 3

    messages: list = [
        SystemMessage(content=AGENT_02_FIX_SYSTEM),
        HumanMessage(content=input_msg),
    ]

    llm = _get_llm_02()
    llm_with_tools = llm.bind_tools(_agent_02_tools)
    tools_by_name  = {getattr(t, "name", ""): t for t in _agent_02_tools}

    logger.info(
        f"Agent02Fix hand-rolled ReAct: tools={list(tools_by_name.keys())}, "
        f"input_msg_chars={len(input_msg)}"
    )

    steps = 0
    rate_retries = 0
    no_progress_streak = 0

    while steps < MAX_STEPS:
        steps += 1
        try:
            ai_response: _AIMessage = llm_with_tools.invoke(messages)
        except Exception as exc:
            err_text = str(exc).lower()
            if ("429" in err_text or "rate" in err_text or "quota" in err_text) and rate_retries < MAX_RATE_RETRIES:
                rate_retries += 1
                backoff = min(15 * (2 ** (rate_retries - 1)), 120)
                logger.warning(
                    f"Agent02Fix rate-limit (step {steps}, retry {rate_retries}/{MAX_RATE_RETRIES}) "
                    f"— backoff {backoff}s"
                )
                _time.sleep(backoff)
                steps -= 1
                continue
            logger.error(f"Agent02Fix step {steps}: LLM error: {exc}")
            raise

        messages.append(ai_response)
        tool_calls = list(getattr(ai_response, "tool_calls", None) or [])

        if not tool_calls:
            no_progress_streak += 1
            logger.info(
                f"Agent02Fix step {steps}: no tool calls "
                f"(streak={no_progress_streak}/{NO_PROGRESS_LIMIT})"
            )
            if no_progress_streak >= NO_PROGRESS_LIMIT:
                break
            continue

        no_progress_streak = 0
        for tc in tool_calls:
            messages.append(_execute_tool_call(tc, tools_by_name))

    counts = _count_tool_calls(messages)
    logger.info(
        f"Agent02Fix hand-rolled ReAct done: steps={steps}, len(messages)={len(messages)}, "
        f"write_terraform_file={counts.get('write_terraform_file', 0)}, "
        f"get_rag_context_for_resource={counts.get('get_rag_context_for_resource', 0)}"
    )
    return messages


def _run_llm_self_review(output_dir: Path) -> dict:
    """LLM self-review pass: the model re-reads its own .tf files and fixes
    structural issues it introduced (Category C fixers):

      1. Cross-reference consistency — hallucinated or dangling resource references,
         duplicate resource declarations across files.
      2. Missing azurerm_resource_group — referenced but never declared.
      3. azurerm_cognitive_account network_acls constraint — custom_subdomain_name
         required when network_acls is present; empty Deny block must be removed.

    This runs BEFORE the deterministic Cat-C fixers so those only act as a
    fallback for the rare case where the LLM self-review misses something.

    Returns a dict with keys: fixes_applied (list), files_unchanged (list), skipped (bool).
    """
    tf_files = sorted(output_dir.glob("*.tf"))
    if not tf_files:
        return {"fixes_applied": [], "files_unchanged": [], "skipped": True, "reason": "no .tf files"}

    # Build a compact snapshot of all generated .tf files for the LLM to review
    file_blocks = []
    for tf in tf_files:
        try:
            content = tf.read_text(encoding="utf-8")
            file_blocks.append(f"=== FILE: {tf.name} ===\n{content}")
        except OSError:
            pass

    if not file_blocks:
        return {"fixes_applied": [], "files_unchanged": [], "skipped": True, "reason": "unreadable files"}

    files_snapshot = "\n\n".join(file_blocks)
    review_prompt = (
        "Below are all the Terraform files I just generated. "
        "Please review them for the three structural issues described in your instructions "
        "and fix any problems found by calling write_terraform_file.\n\n"
        f"{files_snapshot}"
    )

    try:
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        import time

        llm = _get_llm_02()
        llm_with_tools = llm.bind_tools([write_terraform_file, read_generated_files])

        messages = [
            SystemMessage(content=AGENT_02_SELF_REVIEW_SYSTEM),
            HumanMessage(content=review_prompt),
        ]

        MAX_STEPS = 12
        for step in range(MAX_STEPS):
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            tool_calls = getattr(response, "tool_calls", None) or []

            if not tool_calls:
                # LLM finished — extract summary JSON from last message
                break

            for tc in tool_calls:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                call_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                tool_fn = {"write_terraform_file": write_terraform_file, "read_generated_files": read_generated_files}.get(name)
                if tool_fn:
                    try:
                        result = tool_fn.invoke(args)
                    except Exception as e:
                        result = f"ERROR: {e}"
                else:
                    result = f"ERROR: unknown tool {name}"
                from langchain_core.messages import ToolMessage
                messages.append(ToolMessage(content=str(result), tool_call_id=call_id, name=name))

        # Parse summary from the last AIMessage content
        last_content = ""
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                last_content = str(getattr(m, "content", "") or "")
                break

        summary = extract_json(last_content) or {}
        fixes = summary.get("fixes_applied", [])
        unchanged = summary.get("files_unchanged", [])

        if fixes:
            logger.info("LLM self-review: %d fix(es) applied: %s", len(fixes), fixes)
        else:
            logger.info("LLM self-review: no structural issues found")

        return {"fixes_applied": fixes, "files_unchanged": unchanged, "skipped": False}

    except Exception as exc:
        logger.warning("LLM self-review: failed (%s) — deterministic fixers will handle Cat-C issues", exc)
        return {"fixes_applied": [], "files_unchanged": [], "skipped": True, "reason": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# State node functions (called by pipeline_graph.py)
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_02(state: MigrationState) -> dict:
    """LangGraph node: pure-ReAct IaC generation + Python SDK migration.

    Phase 1 — Python: migrate every cloud-SDK file via LLM (no fallback).
    Phase 2 — Terraform: single ReAct invocation generates provider.tf,
              variables.tf, and one <category>.tf per resource. The agent
              calls write_terraform_file for every file — no deterministic
              writes, no text-scraping, no direct-LLM bypass.

    Path 2 (incremental) — scope is limited to:
      - New cloud resources detected in the diff  → new .tf files only
      - Modified Python files detected in the diff → re-migrate those files only
      - provider.tf + variables.tf are always regenerated for consistency

    On hard failure (0 .tf files written), sets needs_human_escalation=True.
    """
    migration_plan      = state.get("migration_plan", {})
    target_cloud        = (state.get("target_cloud") or "").lower()
    _dep_graph          = state.get("dependency_graph") or {}
    source_cloud        = (state.get("source_cloud") or _dep_graph.get("detected_cloud", "")).lower()
    architecture_specs  = dict(state.get("architecture_specs") or {})
    source_files        = state.get("source_files") or {}

    # ── Path 2 — incremental scope ────────────────────────────────────────────
    migration_mode       = state.get("migration_mode") or "full"
    incremental_resources = list(state.get("incremental_resources") or [])
    incremental_files    = list(state.get("incremental_files") or [])

    if migration_mode == "incremental" and incremental_resources:
        # Filter migration plan to only new resources
        all_resources = migration_plan.get("resources", [])
        incremental_plan_resources = [
            r for r in all_resources
            if r.get("terraform_resource") in incremental_resources
            or r.get("target_service") in incremental_resources
            or r.get("target_equivalent") in incremental_resources
        ]
        if incremental_plan_resources:
            migration_plan = {**migration_plan, "resources": incremental_plan_resources}
            logger.info(
                "[Agent02] incremental mode: scoped to %d/%d resources: %s",
                len(incremental_plan_resources), len(all_resources),
                [r.get("terraform_resource") for r in incremental_plan_resources],
            )
        else:
            logger.info("[Agent02] incremental mode: no new resources to generate IaC for")

    if migration_mode == "incremental" and incremental_files:
        # Scope source_files to only changed Python files
        source_files = {
            k: v for k, v in source_files.items()
            if any(k.endswith(f.split("/")[-1]) for f in incremental_files)
        }
        logger.info(
            "[Agent02] incremental mode: scoped Python migration to %d file(s): %s",
            len(source_files), list(source_files.keys()),
        )

    intent_issues: list[str] = list(state.get("intent_issues") or [])
    iac_regen_count = (state.get("iac_regen_count") or 0) + (1 if intent_issues else 0)
    if intent_issues:
        logger.warning(
            f"[Agent02] re-generation triggered by intent issues "
            f"(attempt {iac_regen_count}): {intent_issues}"
        )

    target_region = (
        architecture_specs.get("region")
        or architecture_specs.get("target_region")
        or state.get("target_region")
        or ""
    )
    resource_group = architecture_specs.get("resource_group_name") or "rg-cloud-migrator"
    project_id     = architecture_specs.get("project_id") or ""

    architecture_specs.update({
        "region": target_region,
        "resource_group_name": resource_group,
        "project_id": project_id,
    })

    # Build sdk_changes index: {source_import → target_import}
    sdk_changes_index: dict[str, str] = {}
    for resource in migration_plan.get("resources", []):
        sdk = resource.get("sdk_changes") or {}
        src_import = sdk.get("source_import", "")
        tgt_import = sdk.get("target_import", "")
        if src_import and tgt_import:
            sdk_changes_index[src_import] = tgt_import

    from core.paths import get_output_dir
    migration_id = state.get("migration_id", "unknown")
    output_dir = get_output_dir(migration_id)

    # Override MIGRATION_OUTPUT_DIR so all tool helpers (_get_output_dir() in
    # hcl_merge, react_tools, cicd_tools, …) resolve to the same per-migration
    # directory without requiring a full refactor of every tool signature.
    os.environ["MIGRATION_OUTPUT_DIR"] = str(output_dir)

    # Wipe stale .tf files from a previous attempt on this same migration_id.
    for stale_tf in output_dir.glob("*.tf"):
        try:
            stale_tf.unlink()
        except OSError as e:
            logger.warning(f"Agent02: could not remove stale {stale_tf.name}: {e}")

    # ── IaC guidance: provider version + variable catalogue for Agent 02 ──────
    exec_graph_prompt = build_iac_guidance(
        migration_plan=migration_plan,
        target_cloud=target_cloud,
        target_region=target_region,
        architecture_specs=architecture_specs,
    )
    exec_graph_meta: dict = {}  # no longer carries scaffolding state

    # ── Phase 1: Python SDK migration via LLM (fails hard on errors) ─────────
    resource_sdk_changes = [
        r.get("sdk_changes") or {}
        for r in migration_plan.get("resources", [])
        if r.get("sdk_changes")
    ]
    primary_sdk = resource_sdk_changes[0] if resource_sdk_changes else {}

    # Build a dynamic set of source SDK patterns from two pipeline sources:
    #
    # 1. migration_plan — every breaking_changes[].pattern across all resources
    #    (Agent 01 populated these from RAG + service registry).
    # 2. dependency_graph["python_ai_stack"]["providers_detected"] — class names
    #    detected by the Stack Analyzer's AST scan (e.g. "ChatOpenAI", "Chroma").
    #
    # No static hardcoded list: patterns are specific to what THIS repo uses.
    _dynamic_sdk_patterns: set[str] = set()

    for sdk in resource_sdk_changes:
        if sdk.get("source_import"):
            _dynamic_sdk_patterns.add(sdk["source_import"])
        for bc in sdk.get("breaking_changes", []):
            if bc.get("pattern"):
                _dynamic_sdk_patterns.add(bc["pattern"])

    _dep_graph_for_sdk: dict = state.get("dependency_graph") or {}
    _ai_stack_detected: dict = _dep_graph_for_sdk.get("python_ai_stack") or {}
    for provider_class in _ai_stack_detected.get("providers_detected", []):
        _dynamic_sdk_patterns.add(provider_class)
    # Also add the raw llm_provider string (e.g. "openai") so package imports match
    _llm_provider_str = _ai_stack_detected.get("llm_provider", "")
    if _llm_provider_str:
        _dynamic_sdk_patterns.add(_llm_provider_str)

    # Expand patterns to catch ALL cloud SDK usages in the repo — not just AI/LLM:
    # boto3/botocore (AWS), azure-* SDKs, google-cloud-*, and common DB drivers
    # that use cloud-specific connection strings (psycopg2 with RDS host, etc.)
    _CLOUD_SDK_ALWAYS: dict[str, set[str]] = {
        "aws":   {"boto3", "botocore", "aiobotocore", "s3transfer", "awscrt",
                  "rds.amazonaws.com", "RDS_URL", "S3_BUCKET", "AWS_"},
        "azure": {"azure.storage", "azure.identity", "azure.servicebus",
                  "azure.mgmt", "azure.ai", "azure.keyvault",
                  "AzureOpenAI", "BlobServiceClient", "AZURE_"},
        "gcp":   {"google.cloud", "google.oauth2", "vertexai", "GCS_BUCKET",
                  "GOOGLE_CLOUD", "GOOGLE_APPLICATION_CREDENTIALS"},
    }
    for pat in _CLOUD_SDK_ALWAYS.get(source_cloud.lower(), set()):
        _dynamic_sdk_patterns.add(pat)

    # Fallback: if no breaking_changes from Agent 01 but AI Stack detected an LLM
    # provider, build a synthetic sdk_changes so Phase 1 migration still runs.
    if not primary_sdk and _dynamic_sdk_patterns:
        _AI_SDK_MAP: dict[tuple[str, str], tuple[str, str]] = {
            ("openai",      "azure"): (
                "from openai import OpenAI",
                "from openai import AzureOpenAI",
            ),
            ("openai",      "gcp"):   (
                "from openai import OpenAI",
                "from google.cloud import aiplatform  # Vertex AI",
            ),
            ("openai",      "aws"):   (
                "from openai import OpenAI",
                "import boto3  # Amazon Bedrock bedrock-runtime",
            ),
            ("anthropic",   "aws"):   (
                "import anthropic",
                "import boto3  # Amazon Bedrock (Claude)",
            ),
            ("anthropic",   "azure"): (
                "import anthropic",
                "from openai import AzureOpenAI  # Azure OpenAI GPT-4o nearest equiv",
            ),
            ("huggingface", "azure"): (
                "from transformers import",
                "from azure.ai.ml import MLClient",
            ),
            ("huggingface", "aws"):   (
                "from transformers import",
                "import sagemaker  # SageMaker HuggingFace container",
            ),
        }
        _tc = target_cloud.lower()
        _src_key = _llm_provider_str.lower() if _llm_provider_str else ""
        _src_import, _tgt_import = _AI_SDK_MAP.get((_src_key, _tc), ("", ""))

        # Pull sdk_note from the AI resource in the migration plan (if present)
        _sdk_note = ""
        for _r in migration_plan.get("resources", []):
            if _r.get("category") in ("ai", "vector_db", "llm"):
                _sdk_note = _r.get("sdk_note") or _r.get("reasoning", "")
                break

        if _src_import:
            primary_sdk = {
                "source_import":  _src_import,
                "target_import":  _tgt_import,
                "breaking_changes": [],
                "sdk_note": _sdk_note,
            }
            _dynamic_sdk_patterns.add(_src_import)
            logger.info(
                "Agent02: primary_sdk was empty — built from AI Stack (%s→%s for %s→%s)",
                _src_import, _tgt_import, source_cloud, target_cloud,
            )

    logger.info(
        f"Agent02 SDK detection: {len(_dynamic_sdk_patterns)} dynamic pattern(s) "
        f"from migration plan + Stack Analyzer AST scan"
    )

    def _has_cloud_sdk(file_content: str) -> bool:
        return any(pat in file_content for pat in _dynamic_sdk_patterns)

    # Build one comprehensive migration context for ALL files in this migration.
    # This replaces the single primary_sdk approach — the LLM gets the full picture
    # and applies only what's relevant to each specific file.
    _comprehensive_ctx = _build_comprehensive_migration_context(
        migration_plan=migration_plan,
        source_cloud=source_cloud,
        target_cloud=target_cloud,
        ai_stack=_ai_stack_detected,
    )

    patched_py_files: list[str] = []
    for file_path, content in source_files.items():
        filename = Path(file_path).name
        # Preserve directory structure: use full relative path from repo root
        out_path = output_dir / Path(file_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        is_python = filename.endswith(".py") or filename.endswith(".ipynb")
        is_requirements = filename == "requirements.txt"
        is_env = filename in (".env", ".env.example", ".env.template")

        if is_python and _has_cloud_sdk(content):
            # Migrate using comprehensive context — no primary_sdk gate needed
            migrated = _migrate_python_sdk_with_llm(
                filename, content, primary_sdk or {}, source_cloud, target_cloud,
                migration_context=_comprehensive_ctx,
            )
        elif is_requirements:
            migrated = _migrate_requirements_txt(content, source_cloud, target_cloud)
        elif is_env:
            migrated = _migrate_env_file(content, source_cloud, target_cloud)
        else:
            migrated = content
            logger.debug(f"Agent02: copied {file_path} (no migration needed)")

        try:
            out_path.write_text(migrated, encoding="utf-8")
            patched_py_files.append(file_path)
        except Exception as e:
            logger.warning(f"Agent02: could not write {file_path}: {e}")

    # ── Phase 2: Terraform — single-pass ReAct ───────────────────────────────
    # Graph RAG: structural source (argument names, topology, cross-references).
    # SecurityPolicyEngine: security source (mandatory attribute values).
    # RAG is enriched with SPE invariants BEFORE reaching the LLM.
    full_rag_context, _dep_block, rag_traversal, _per_resource = _get_rag_context(migration_plan)

    _spe = _SPE()
    full_rag_context = _spe.enrich_rag_context(full_rag_context, migration_plan)
    _security_block  = _spe.get_security_prompt_block(migration_plan)
    _spe_coverage    = _spe.get_rule_coverage_report(migration_plan)
    logger.info(
        f"SecurityPolicyEngine: coverage={_spe_coverage['coverage_pct']}% "
        f"({len(_spe_coverage['covered_by_spe'])}/{_spe_coverage['total_resources']} resources)"
    )

    # Consume security violations from a previous security-regen cycle
    security_violations: list[str] = list(state.get("security_violations") or [])
    security_regen_count: int = state.get("security_regen_count") or 0

    resources_to_generate: list[dict] = []
    for r in migration_plan.get("resources", []):
        strategy = (r.get("strategy") or r.get("strategy_7r") or "").upper()
        if strategy in {"RETAIN", "RETIRE"}:
            continue
        target_svc = (
            r.get("target_service") or r.get("target_equivalent")
            or r.get("terraform_resource") or ""
        )
        src_svc = (
            r.get("source_service") or r.get("service")
            or r.get("resource_name") or ""
        )
        category = r.get("type") or r.get("category") or _infer_category(target_svc)
        resources_to_generate.append({
            "source": src_svc,
            "target": target_svc,
            "category": category,
            "strategy": strategy,
        })

    expected_tf_count = len(resources_to_generate)
    provider_name = {"azure": "azurerm", "gcp": "google", "aws": "aws"}.get(target_cloud, target_cloud)

    # Pretty-print the resource list for the prompt
    resources_table_lines = [
        "| # | source | target (terraform_resource) | category | strategy |",
        "|---|--------|----------------------------|----------|----------|",
    ]
    for idx, r in enumerate(resources_to_generate, 1):
        resources_table_lines.append(
            f"| {idx} | {r['source']} | {r['target']} | {r['category']} | {r['strategy']} |"
        )
    resources_table = "\n".join(resources_table_lines)

    intent_section = ""
    if intent_issues:
        violations = "\n".join(f"  - {v}" for v in intent_issues)
        intent_section = (
            "\n## ⚠️ INTENT VIOLATIONS — must be fixed in this regeneration\n"
            f"{violations}\n"
            f"Use `location = \"{target_region}\"` (via var.location for Azure) "
            f"in every resource block. Do not use any other region value.\n"
        )

    security_regen_section = ""
    if security_violations:
        violation_lines = "\n".join(f"  - {v}" for v in security_violations)
        security_regen_section = (
            "\n## 🔒 SECURITY VIOLATIONS from previous generation — MUST FIX\n"
            f"The previous Terraform output failed Checkov on these checks:\n"
            f"{violation_lines}\n"
            f"Apply the SecurityPolicyEngine rules above to fix every violation.\n"
            f"Checkov will re-run after generation — all listed violations must be resolved.\n"
        )

    _dep_section = f"\n{_dep_block}\n" if _dep_block else ""

    input_msg = (
        f"## Migration plan to implement\n"
        f"target_cloud:         {target_cloud}\n"
        f"provider:             {provider_name}\n"
        f"region:               {target_region}\n"
        f"resource_group_name:  {resource_group}\n"
        f"project_id:           {project_id or '(not applicable)'}\n\n"
        f"{exec_graph_prompt}\n"
        f"{intent_section}"
        f"{security_regen_section}"
        f"## Resources to generate ({expected_tf_count} total)\n"
        f"{resources_table}\n\n"
        f"{_dep_section}"
        f"{_security_block}\n\n"
        f"## GRAPH RAG DOCUMENTATION (structural reference — argument names and types ONLY)\n"
        f"## NOTE: Security attribute VALUES come from SECURITY INVARIANTS above, not from here.\n"
        f"{full_rag_context}\n\n"
        f"## Your job — execute the ReAct loop\n"
        f"For each resource above (follow the generation order in the Execution Graph):\n"
        f"  1. Check SECURITY INVARIANTS above for mandatory attributes.\n"
        f"  2. Check STRUCTURAL DEPENDENCIES above — if a resource lists companions, generate them too.\n"
        f"  3. get_rag_context_for_resource → compose HCL (use var.NAME from Execution Graph).\n"
        f"  4. validate_terraform_block → write_terraform_file('<category>.tf') → read_generated_files.\n"
        f"Group resources by category — append to existing files instead of creating new ones.\n"
        f"Start with provider.tf and variables.tf (use values from the Execution Graph above).\n"
        f"When ALL files (provider.tf, variables.tf, and every resource file) are on disk, return the FINAL ANSWER JSON.\n"
        f"\nReminder: text in your reply is ignored. Only write_terraform_file persists files.\n"
    )

    generation_errors: list[str] = []

    try:
        messages = _run_iac_react_loop(input_msg, output_dir, expected_tf_count, _agent_02_tools)

        # ── LLM self-review (Category C — structural issues the model can fix itself) ──
        # Runs BEFORE deterministic passes so fixers only act as fallback.
        # Covers: cross-reference consistency, missing resource_group, cognitive_network_acls.
        self_review_result = _run_llm_self_review(output_dir)
        if self_review_result.get("skipped"):
            logger.info(
                "LLM self-review skipped (%s) — deterministic Cat-C fixers will handle structural issues",
                self_review_result.get("reason", "unknown"),
            )

        # Deterministic post-passes (order matters):
        #   Cat-A (invariants — must always run):
        #     1. variable defaults — patch known wrong defaults, add safe defaults
        #     2. provider versions — rewrite stale `~> 3.0` to current floor
        #     3. missing var declarations — auto-declare every var.X reference
        #     4. deprecated azurerm attrs — rename removed storage attributes
        #     5. postgresql B-tier HA — remove invalid high_availability from B_Standard_*
        #     6. security attr injection — mandatory Checkov-required attributes
        #   Cat-C fallback (runs after LLM self-review — catches anything the LLM missed):
        #     7. cross-references — drop orphan blocks, dedupe declarations
        #     8. ensure_resource_group — inject azurerm_resource_group if still missing
        #     9. cognitive_network_acls — custom_subdomain_name + empty Deny block
        _fix_tf_variables(output_dir)
        _normalize_provider_versions(output_dir)
        _add_missing_variable_declarations(output_dir, target_region=target_region)
        _prune_orphan_required_variables(output_dir)
        _fix_deprecated_azurerm_attrs(output_dir)
        _fix_postgresql_bsku_ha(output_dir)
        _fix_postgresql_timeouts(output_dir)
        _ensure_required_business_attrs(output_dir)   # inject version/storage/admin attrs
        _inject_missing_security_attrs(output_dir)    # inject Checkov security attrs
        # Cat-C fallback — only fixes what the LLM self-review may have missed
        _fix_tf_cross_references(output_dir)
        _ensure_resource_group(output_dir)
        _fix_cognitive_network_acls(output_dir)
        _fix_resource_group_references(output_dir)    # add implicit RG dep (prevents ResourceGroupNotFound)
        _fix_role_assignment_permissions(output_dir)  # gate role assignments (AuthorizationFailed 403 guard)
        data_residency            = architecture_specs.get("data_residency") or state.get("data_residency_requirement") or ""
        compliance_standards_list = list(state.get("compliance_standards") or [])
        ha_required               = bool(state.get("high_availability_required", False))
        net_isolation             = bool(state.get("network_isolation_required", False))

        region_adjustments = _fix_region_availability(
            output_dir,
            target_region=target_region,
            data_residency=data_residency,
        )
        _fix_ha_requirement(output_dir, high_availability_required=ha_required)
        _fix_network_isolation(output_dir, network_isolation_required=net_isolation)
        _fix_compliance_standards(output_dir, compliance_standards=compliance_standards_list)
        _fix_deprecated_azurerm_attrs(output_dir)     # re-run after compliance: removes attrs compliance may have re-injected (e.g. infrastructure_encryption_enabled)
        _fix_provider_config(output_dir)              # fix resource_provider_registrations type + remove _providers.tf doublon
        _fix_storage_sas_policy_nesting(output_dir)   # fix infrastructure_encryption_enabled inside sas_policy
        _fix_globally_unique_names(output_dir)        # ensure storage account name is globally unique (before orphan preamble fix)
        _fix_corrupted_string_literals(output_dir)    # strip junk after closing quote (e.g. "name"extra")
        _fix_orphan_preamble(output_dir)              # drop content before first block (LLM name corruption residue)
        _fix_stray_chars_after_braces(output_dir)     # remove LLM }e / orphan } artifacts before validate
        _fix_glued_hcl_arguments(output_dir)          # split "geo_redundant_backup_enabled = true  version = ..." onto separate lines
        _fix_cognitive_network_acls(output_dir)       # fix network_acls + custom_subdomain_name constraint
        _inject_azure_network(output_dir)             # VNet + subnets + PEs + DNS + role_assignment
        # Re-run AFTER _inject_azure_network: its Fix 5 (forces
        # public_network_access_enabled = false when delegated_subnet_id is present)
        # is a no-op on the first pass above because _inject_azure_network is what
        # actually wires delegated_subnet_id/private_dns_zone_id into the Postgres
        # block. Without this second pass, Azure rejects the apply with
        # "ConflictingPublicNetworkAccessAndVirtualNetworkConfiguration".
        _fix_postgresql_bsku_ha(output_dir)
        _fix_postgresql_timeouts(output_dir)              # inject timeouts block — Azure PG can take 40+ min
        _disable_role_assignments_by_default(output_dir)  # SP lacks roleAssignments/write — keep count=0
        # Second pass: _fix_role_assignment_permissions and _inject_azure_network above may have
        # introduced var.enable_role_assignments references that weren't present when the first
        # _add_missing_variable_declarations ran — re-run to catch them.
        _add_missing_variable_declarations(output_dir, target_region=target_region)
        _ensure_health_check_outputs(output_dir)      # outputs.tf: fqdn + storage_name + cognitive_name

        # ── tfgraph_importer: enrich RAG graph with real DEPENDS_ON edges ────────
        # After generation, run `terraform graph` on the output directory to extract
        # real inter-resource dependencies and insert them into resource_relations.
        # This feeds back into future migrations' Neo4j traversals.
        try:
            from rag.tfgraph_importer import import_from_directory
            n_edges = import_from_directory(str(output_dir))
            if n_edges:
                logger.info("tfgraph_importer: %d DEPENDS_ON edges inserted from terraform graph", n_edges)
        except Exception as _tgi_err:
            logger.debug("tfgraph_importer skipped: %s", _tgi_err)

        disk_tf_files = sorted(p.name for p in output_dir.glob("*.tf"))
        terraform_code = _read_generated_tf_files()
        tool_call_counts = _count_tool_calls(messages)

        # Dump forensic JSON only when DEBUG_AGENT is set (avoids disk I/O in prod)
        if os.getenv("DEBUG_AGENT", "").lower() in ("1", "true", "yes"):
            _save_debug_messages(
                output_dir,
                messages,
                metadata={
                    "iteration_count": "<=3",
                    "expected_tf_resource_count": expected_tf_count,
                    "files_on_disk": disk_tf_files,
                    "tool_call_counts": tool_call_counts,
                    "target_cloud": target_cloud,
                    "region": target_region,
                    "intent_regen": iac_regen_count,
                },
            )

        # Soft warning if ReAct produced no .tf files — continue rather than escalate.
        resource_tf_files = disk_tf_files
        if not resource_tf_files:
            logger.warning("Agent02: 0 resource .tf files generated — continuing with quality warning")
            generation_errors.append(
                "Agent 02 ReAct loop produced 0 resource .tf files. "
                "Verify AZURE_AI_ENDPOINT / AZURE_AI_API_KEY / AZURE_MODEL."
            )

        # Soft warning if some resources missing — count actual `resource` blocks,
        # not files. Agent 02 groups multiple resources per category file
        # (storage.tf, compute.tf, …), so a file count would falsely report
        # missing resources even on a perfect run.
        # Use unique TARGET resource types as the floor — multiple source resources
        # can legitimately consolidate to a single target (e.g. 4 S3 sub-resources
        # → 1 azurerm_storage_account). Comparing raw plan length (17) to generated
        # blocks (12) would always trigger a false positive for normal migrations.
        unique_target_types = len({
            r.get("target", "") for r in resources_to_generate if r.get("target")
        })
        min_expected = max(1, unique_target_types)
        resource_block_count = len(re.findall(
            r'^\s*resource\s+"', terraform_code, re.MULTILINE
        ))
        missing_resources = max(0, min_expected - resource_block_count)
        if missing_resources > 0:
            generation_errors.append(
                f"{missing_resources} unique resource type(s) missing "
                f"({resource_block_count} blocks generated, {min_expected} unique target types expected) — "
                "validate_iac will surface specifics."
            )

        all_generated = sorted(set(disk_tf_files + patched_py_files))

        # Build user-facing region adjustment notices
        # (populated by _fix_region_availability when resources are rerouted)
        region_notices: list[dict] = []
        for resource_type, files in (region_adjustments or {}).items():
            if resource_type.startswith("BLOCKED:"):
                region_notices.append({
                    "level": "error",
                    "resource_type": resource_type.replace("BLOCKED:", ""),
                    "message": files[0] if files else "No supported region within data residency constraint",
                })
            else:
                region_notices.append({
                    "level": "warning",
                    "resource_type": resource_type,
                    "files": files,
                    "message": (
                        f"{resource_type} was automatically moved to a supported region "
                        f"(not available in '{target_region}'). Check region_adjustments."
                    ),
                })

        return {
            "generated_code_files": all_generated,
            "iac_quality_warning": len(generation_errors) > 0 or any(n["level"] == "error" for n in region_notices),
            "iac_regen_count": iac_regen_count,
            "needs_security_regen": False,
            "security_violations": [],
            "artifacts": {
                **(state.get("artifacts") or {}),
                "generation_summary": {
                    "generated_files": disk_tf_files,
                    "expected_count": expected_tf_count,
                    "tool_call_counts": tool_call_counts,
                },
                "region_adjustments": region_adjustments or {},
                "region_notices": region_notices,
                "generation_errors": generation_errors,
                "terraform_code": terraform_code,
                "terraform_files": disk_tf_files,
                "expected_tf_resource_count": expected_tf_count,
                "patched_python_files": patched_py_files,
                "sdk_changes_applied": sdk_changes_index,
                "rag_graph_traversal": rag_traversal,
                "iac_quality_warning": len(generation_errors) > 0,
                "rag_context_available": True,
                "tool_call_counts": tool_call_counts,
                "execution_graph_meta": exec_graph_meta,
                "spe_coverage": _spe_coverage,
                "security_regen_count": security_regen_count,
            },
            "correction_counts": {},
            "modules_to_fix": [],
        }

    except Exception as e:
        logger.error(f"Agent02 failed (non-blocking): {e}", exc_info=True)
        rag_was_unavailable = isinstance(e, RuntimeError) and "GraphRAG" in str(e)
        _meta = locals().get("exec_graph_meta") or {}
        return {
            "generated_code_files": sorted(patched_py_files),
            "needs_human_escalation": False,
            "needs_security_regen": False,
            "security_violations": [],
            "iac_quality_warning": True,
            "correction_counts": {},
            "modules_to_fix": [],
            "artifacts": {
                **(state.get("artifacts") or {}),
                "generation_error": str(e),
                "generation_errors": [str(e)],
                "terraform_code": "",
                "terraform_files": [],
                "expected_tf_resource_count": _count_resources_requiring_terraform(migration_plan),
                "patched_python_files": patched_py_files,
                "iac_quality_warning": True,
                "rag_context_available": not rag_was_unavailable,
                "execution_graph_meta": _meta,
            },
        }


def run_agent_02_fix(state: MigrationState) -> dict:
    """LangGraph node: fix only the modules that failed IaC validation.

    All failed modules — including provider.tf and variables.tf — are sent
    to the Agent 02 LLM fix loop. Ordering: provider/variables first so
    resource files have correct var references when they are fixed.
    Layer order: network → iam → storage → database → compute → messaging → monitoring.
    """
    modules_to_fix    = list(state.get("modules_to_fix") or [])
    correction_counts = state.get("correction_counts") or {}
    migration_plan    = state.get("migration_plan", {})
    target_cloud      = (state.get("target_cloud") or "").lower()

    if not modules_to_fix:
        return {}

    updated_counts = dict(correction_counts)
    for module in modules_to_fix:
        updated_counts[module] = updated_counts.get(module, 0) + 1

    # ── Sort modules by infrastructure layer (provider/variables first, then network → monitoring) ──
    # provider.tf and variables.tf go first so resource files have correct var references.
    resource_modules = list(modules_to_fix)
    _LAYER_ORDER = ["network", "iam", "storage", "database", "compute", "messaging", "monitoring"]

    def _layer_key(filename: str) -> int:
        base = filename.replace(".tf", "")
        try:
            return _LAYER_ORDER.index(base)
        except ValueError:
            return len(_LAYER_ORDER)

    resource_modules = sorted(resource_modules, key=_layer_key)

    if any(updated_counts.get(m, 0) >= 3 for m in resource_modules):
        # Max-attempts reached — let validate_iac decide (accept if tf validate passes).
        logger.warning(
            f"Agent02Fix: max corrections reached for {resource_modules} — "
            "deferring escalation decision to validate_iac"
        )
        return {"correction_counts": updated_counts}

    iac_validation   = (state.get("artifacts") or {}).get("iac_validation", {})
    tf_diagnostics   = iac_validation.get("terraform_validation", {}).get("diagnostics", [])
    checkov_failures = iac_validation.get("security_scan", {}).get("failed_checks", [])
    plan_output      = iac_validation.get("plan_dryrun", {}).get("plan_output", "")

    error_taxonomy = _classify_tf_errors(tf_diagnostics, checkov_failures, plan_output)
    error_context  = error_taxonomy["summary"]

    # Inject real terraform plan error from the runner (stage=plan failures routed back here)
    runner_failure_detail = state.get("runner_failure_detail") or {}
    runner_plan_error = ""
    if runner_failure_detail.get("stage") == "plan":
        raw = runner_failure_detail.get("raw_excerpt") or runner_failure_detail.get("message") or ""
        resource = runner_failure_detail.get("resource", "")
        file_location = runner_failure_detail.get("file_location", "")
        error_class = runner_failure_detail.get("class", "")
        if raw:
            # Build a structured, explicit diagnosis to guide Agent 02
            diagnosis = ""
            if "cannot parse" in raw.lower() and "empty" in raw.lower():
                diagnosis = (
                    "DIAGNOSIS: A variable passed to a resource attribute is an empty string.\n"
                    "This usually means a variable has default=\"\" and is NOT set by tfvars.\n"
                    "FIX: Replace the var.X reference with a direct resource reference.\n"
                    "Example: if 'application_insights_id = var.application_insights_id' fails,\n"
                    "check if azurerm_application_insights.X is declared in the same file;\n"
                    "if so, replace with 'application_insights_id = azurerm_application_insights.X.id'.\n"
                )
            elif "invalid resource id" in raw.lower() or "expected a valid azure resource id" in raw.lower():
                diagnosis = (
                    "DIAGNOSIS: An Azure resource ID argument received an empty or malformed value.\n"
                    "FIX: Replace var.X references that default to \"\" with direct resource.type.name.id references.\n"
                )
            runner_plan_error = (
                f"\n=== REAL TERRAFORM PLAN ERROR (from cloud provider — HIGHEST PRIORITY) ===\n"
                f"Error class : {error_class}\n"
                f"Resource    : {resource}\n"
                f"File/line   : {file_location or 'see excerpt below'}\n"
                f"{diagnosis}"
                f"--- Raw terraform stderr ---\n"
                f"{raw.strip()}\n"
                f"--- End stderr ---\n"
                f"Fix this error first — it blocks actual deployment.\n"
                f"Use read_terraform_file to inspect the file before writing the fix.\n"
                f"=== END PLAN ERROR ===\n"
            )
            logger.info(
                "Agent02Fix: injecting runner plan error class=%s resource=%s location=%s",
                error_class, resource, file_location,
            )

    logger.info(
        f"Agent02Fix: classified {error_taxonomy['total_count']} tf errors, "
        f"{len(error_taxonomy['security_errors'])} security failures — "
        f"resource modules to fix (ordered): {resource_modules}"
    )

    provider_name_fix = {"azure": "azurerm", "gcp": "google", "aws": "aws"}.get(target_cloud, target_cloud)

    input_msg = (
        f"Fix these failed IaC resource modules: {resource_modules}\n\n"
        f"target_cloud: {target_cloud}\n\n"
        f"{runner_plan_error}"
        f"=== VALIDATION ERRORS FROM PREVIOUS ATTEMPT ===\n"
        f"{error_context}\n"
        f"=== END ERRORS ===\n\n"
        f"migration_plan:\n{json.dumps(migration_plan, indent=2)}\n\n"
        f"## Fix Instructions (read carefully — ordered by priority)\n\n"
        f"1. SCHEMA errors marked '⚡ APPLY DIRECTLY': the error already contains the correct "
        f"attribute name. Rename it in the file immediately using write_terraform_file — "
        f"DO NOT call get_rag_context_for_resource for these, that wastes a step.\n\n"
        f"2. SCHEMA errors marked '⚡ ACTION (call RAG)': call "
        f"get_rag_context_for_resource('{provider_name_fix}', '<resource_type>') to find "
        f"the correct argument name, then fix it.\n\n"
        f"3. SECURITY failures: each entry shows REQUIREMENT (what to enforce) and "
        f"'failing attributes' (the exact HCL path that failed). For each:\n"
        f"   a. Call get_rag_context_for_resource('{provider_name_fix}', '<resource_type>') "
        f"to find the correct argument syntax.\n"
        f"   b. Add or correct the failing attribute in the resource block to satisfy "
        f"the REQUIREMENT.\n"
        f"   c. Write the fixed file with write_terraform_file.\n"
        f"   Fix EVERY security failure listed — do not skip any.\n\n"
        f"4. If provider.tf or variables.tf are in the failed list: rewrite them completely "
        f"using the correct provider version and variable declarations from the migration plan.\n\n"
        f"5. CRITICAL TYPE RULES — violations cause terraform validate to fail:\n"
        f"   - azurerm_role_assignment: `count` MUST be `var.enable_role_assignments` (number).\n"
        f"     NEVER write `count = true` or `count = false` — those are booleans and Terraform\n"
        f"     rejects them with 'number required, but have bool'.\n"
        f"   - provider azurerm: NEVER write `skip_provider_registration` — write\n"
        f"     `resource_provider_registrations = \"none\"` instead.\n"
        f"   - variables.tf: NEVER declare the same variable twice — check existing declarations\n"
        f"     before adding new ones.\n\n"
        f"6. Use write_terraform_file to save each fixed file.\n"
        f"7. Return a final JSON summary."
    )

    try:
        output_dir_fix = Path(_get_output_dir())
        messages = _run_fix_react_loop(input_msg, output_dir_fix)
        if not messages:
            raise RuntimeError("Agent02Fix returned no messages from LLM")

        last_msg = messages[-1]
        content_obj = getattr(last_msg, "content", last_msg)
        if isinstance(content_obj, list):
            content = "\n".join(str(p) for p in content_obj)
        else:
            content = str(content_obj)
        summary = extract_json(content)

        # Re-run the deterministic post-passes after the LLM's fix edits so a
        # newly-introduced var.X reference, stale provider version, or orphan
        # cross-reference doesn't survive into the next validate_iac call.
        try:
            architecture_specs = state.get("architecture_specs") or {}
            target_region_fix = (
                architecture_specs.get("region")
                or architecture_specs.get("target_region")
                or state.get("target_region")
                or ""
            )
            # Deduplicate variables.tf first — the LLM fix loop may have appended
            # duplicate variable blocks, which cause terraform to fail with
            # 'duplicate variable definition' on the next validate_iac call.
            vars_file_fix = output_dir_fix / "variables.tf"
            if vars_file_fix.exists():
                try:
                    _vtext = vars_file_fix.read_text(encoding="utf-8")
                    _vtext_dedup = _deduplicate_variable_blocks(_vtext)
                    if _vtext_dedup != _vtext:
                        vars_file_fix.write_text(_vtext_dedup, encoding="utf-8")
                        logger.info("Agent02Fix: deduplicated variables.tf before post-passes")
                except OSError:
                    pass
            _fix_tf_cross_references(output_dir_fix)
            _fix_tf_variables(output_dir_fix)
            _normalize_provider_versions(output_dir_fix)
            _add_missing_variable_declarations(output_dir_fix, target_region=target_region_fix)
            _prune_orphan_required_variables(output_dir_fix)  # drop dead var.X with no default & no reference
            _fix_deprecated_azurerm_attrs(output_dir_fix)
            _fix_postgresql_bsku_ha(output_dir_fix)   # re-run: LLM fix may revert bare SKU
            _fix_postgresql_timeouts(output_dir_fix)
            _ensure_required_business_attrs(output_dir_fix)
            _inject_missing_security_attrs(output_dir_fix)
            _ensure_resource_group(output_dir_fix)
            _fix_resource_group_references(output_dir_fix)
            _fix_role_assignment_permissions(output_dir_fix)  # re-guard (LLM may re-emit the block)
            _fix_region_availability(output_dir_fix, target_region=target_region_fix)
            _fix_provider_config(output_dir_fix)
            _fix_storage_sas_policy_nesting(output_dir_fix)
            _fix_globally_unique_names(output_dir_fix)    # must run before orphan preamble fix
            _fix_corrupted_string_literals(output_dir_fix)  # strip junk after closing quote (e.g. "name"extra")
            _fix_orphan_preamble(output_dir_fix)          # drop content before first block (LLM name corruption residue)
            _fix_stray_chars_after_braces(output_dir_fix)
            _fix_glued_hcl_arguments(output_dir_fix)      # split glued "attr = val  attr2 = val2" lines (e.g. database.tf line 12)
            _fix_cognitive_network_acls(output_dir_fix)
            _fix_ha_requirement(output_dir_fix, high_availability_required=bool(state.get("high_availability_required", False)))
            _fix_network_isolation(output_dir_fix, network_isolation_required=bool(state.get("network_isolation_required", False)))
            _fix_compliance_standards(output_dir_fix, compliance_standards=list(state.get("compliance_standards") or []))
            _fix_deprecated_azurerm_attrs(output_dir_fix)  # re-run after compliance: removes attrs re-injected by compliance (e.g. infrastructure_encryption_enabled)
            _inject_azure_network(output_dir_fix)        # VNet + subnets + PEs + DNS + role_assignment
            # Re-run AFTER _inject_azure_network — see comment on the first-pass call:
            # delegated_subnet_id only exists in the block once network injection runs,
            # so Fix 5 (force public_network_access_enabled = false) must run again here.
            _fix_postgresql_bsku_ha(output_dir_fix)
            _fix_postgresql_timeouts(output_dir_fix)
            _disable_role_assignments_by_default(output_dir_fix)  # SP lacks roleAssignments/write — keep count=0
            # Second pass: _fix_role_assignment_permissions and _inject_azure_network above may have
            # introduced var.enable_role_assignments references after the first
            # _add_missing_variable_declarations ran — re-run to catch them.
            _add_missing_variable_declarations(output_dir_fix, target_region=target_region_fix)
            _ensure_health_check_outputs(output_dir_fix) # outputs.tf: fqdn + storage + cognitive
        except Exception as post_e:
            logger.warning(f"Agent02Fix: post-pass failed (non-blocking): {post_e}")

        terraform_code = _read_generated_tf_files()

        return {
            "correction_counts": updated_counts,
            "needs_human_escalation": False,
            "artifacts": {
                **(state.get("artifacts") or {}),
                "last_fix_summary": summary,
                "terraform_code": terraform_code,
            },
        }

    except Exception as e:
        logger.error(f"Agent02Fix failed: {e}", exc_info=True)
        return {
            "correction_counts": updated_counts,
            "needs_human_escalation": False,
            "artifacts": {
                **(state.get("artifacts") or {}),
                "fix_error": str(e),
            },
        }
