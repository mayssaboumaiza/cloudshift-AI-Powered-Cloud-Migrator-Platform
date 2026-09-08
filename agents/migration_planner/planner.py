"""
planner.py — Agent 01: Cloud Migration Planning (LangGraph node).

Entry points:
  run_agent_01()            — LangGraph node: plans the full migration from a dependency graph.
  run_agent_01_correction() — Re-run planning for user-rejected services only.

Pipeline position:
  Input  : state["dependency_graph"]  (dict produced by stack_analyzer)
  Output : {"migration_plan": dict, "artifacts": {"preview_report": str}}

Backward compatibility with Agent 02/03:
  migration_plan["resources"] == migration_plan["services"]  (same list, two keys)
  Each service includes: resource_name, strategy, sdk_changes.

Smoke-test:
  state = {
      "dependency_graph": {
          "detected_cloud": "aws",
          "resources": {"nodes": [
              {"id": "s3", "type": "storage", "score": 8, "cloud_confirmed": True}
          ], "edges": []}
      },
      "target_cloud": "gcp",
  }
  result = run_agent_01(state)
  assert "migration_plan" in result
  assert "preview_report" in result["artifacts"]
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, ".env"))

import httpx
from langchain_openai import AzureChatOpenAI
from langgraph.prebuilt import create_react_agent

from agents.pipeline_state import MigrationState
from agents.migration_planner.system_prompt import AGENT_01_SYSTEM
from agents.migration_planner.plan_validation import (
    _VALID_STRATEGIES,
    _validate_plan_inline,
    _normalize_service,
    _normalize_resources,
)
from agents.migration_planner.plan_metrics import (
    _batch_by_category,
    _assess_complexity,
    _compute_dynamic_weeks,
    _compute_savings,
)
from agents.migration_planner.plan_execution import (
    _extract_migration_plan,
    _run_batch_with_react_loop,
)
from agents.migration_planner.tools_scoring import (
    check_service_maturity,
    decide_7r_strategy,
    decide_7r_strategy_v2,
    score_service_candidates,
    lookup_terraform_mapping,
)
from agents.migration_planner.tools_live import (
    find_equivalent_service,
    get_pricing,
    search_provider_docs,
)
from agents.migration_planner.intent_validator import (
    validate_migration_intent,
    mismatches_to_issues,
)
from agents.migration_planner.audit_logger import (
    log_migration_decisions,
    detect_planning_errors,
)
from configuration.settings import settings

logger = logging.getLogger("Agent01")


# ─────────────────────────────────────────────────────────────────────────────
# LLM singleton (Azure OpenAI GPT-4o) — thread-safe
# ─────────────────────────────────────────────────────────────────────────────

_AZURE_TIMEOUT = httpx.Timeout(timeout=float(os.getenv("AZURE_REQUEST_TIMEOUT", "180")), connect=15.0)

_llm: AzureChatOpenAI | None = None
_llm_lock = threading.Lock()


def _get_llm() -> AzureChatOpenAI:
    """Return the cached LLM instance, creating it on first call."""
    global _llm
    with _llm_lock:
        if _llm is None:
            # Deployment name read from .env (AZURE_MODEL_01 → AZURE_MODEL → settings default = gpt-5.1)
            deployment = os.getenv("AZURE_MODEL_01") or os.getenv("AZURE_MODEL") or os.getenv("AZURE_OPENAI_DEPLOYMENT") or settings.AZURE_MODEL
            logger.info(f"Agent01: initializing LLM deployment={deployment}")
            _llm = AzureChatOpenAI(
                azure_endpoint=os.getenv("AZURE_AI_ENDPOINT") or os.getenv("AZURE_OPENAI_ENDPOINT", ""),
                api_key=os.getenv("AZURE_AI_API_KEY") or os.getenv("AZURE_OPENAI_API_KEY", ""),
                azure_deployment=deployment,
                api_version=os.getenv("AZURE_OPENAI_API_VERSION") or settings.AZURE_OPENAI_API_VERSION,
                temperature=0,
                max_tokens=4096,
                timeout=_AZURE_TIMEOUT,
            )
    return _llm


# ─────────────────────────────────────────────────────────────────────────────
# JSON extraction helper (used by plan_execution, re-exported for tests)
# ─────────────────────────────────────────────────────────────────────────────

def extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a free-form text string (LLM output parser)."""
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


# ─────────────────────────────────────────────────────────────────────────────
# Preview report generator (deterministic — no LLM call)
# ─────────────────────────────────────────────────────────────────────────────

def _gen_preview_report(
    migration_plan_json: str,
    source_cloud: str,
    target_cloud: str,
    framework: str = "",
    vector_db: str = "",
) -> str:
    """Generate a markdown preview report from a migration plan dict.

    Used by run_agent_01() to populate the 'preview_report' artifact shown
    to the user in the human-approval step before they accept or reject.
    """
    try:
        plan = json.loads(migration_plan_json) if isinstance(migration_plan_json, str) else migration_plan_json
    except (json.JSONDecodeError, TypeError):
        plan = {}

    resources = plan.get("resources") or plan.get("services", [])
    summary   = plan.get("summary", {})
    note      = plan.get("note", "")

    # Multi-cloud source: derive readable label from individual service clouds
    if source_cloud.lower() == "multi":
        providers = sorted({
            r.get("source_cloud", r.get("cloud", "")).upper()
            for r in resources
            if r.get("source_cloud") or r.get("cloud")
        } - {""})
        src = "+".join(providers) if providers else "MULTI"
    else:
        src = source_cloud.upper()
    tgt = target_cloud.upper()

    lines = [f"# Plan de migration {src} → {tgt}\n"]

    if note:
        lines.append(f"> ⚠️ {note}\n")
    if framework:
        lines.append(f"**Framework IA détecté** : {framework}")
    if vector_db:
        lines.append(f"**Base vectorielle** : {vector_db}\n")

    total_services = len(resources)
    total_eur = summary.get("total_monthly_eur") or sum(
        r.get("monthly_cost_eur", 0) or r.get("monthly_cost_estimate", 0) or 0
        for r in resources
    )

    lines.append(f"\n## Résumé\n")
    lines.append(f"- **Services** : {total_services}")
    lines.append(f"- **Coût cible estimé** : {round(total_eur, 2)} €/mois\n")

    if resources:
        lines.append("## Services planifiés\n")
        lines.append("| Service source | Service cible | Stratégie 7R | Coût €/mois |")
        lines.append("|---|---|---|---|")
        for r in resources:
            src_svc  = r.get("source_service") or r.get("resource_name", "—")
            tgt_svc  = r.get("target_service", "—")
            strategy = r.get("strategy_7r") or r.get("strategy", "—")
            cost     = round(r.get("monthly_cost_eur", 0) or r.get("monthly_cost_estimate", 0) or 0, 2)
            lines.append(f"| {src_svc} | {tgt_svc} | {strategy} | {cost} |")

    deployment_order = plan.get("deployment_order", [])
    if deployment_order:
        lines.append(f"\n## Ordre de déploiement\n")
        for i, svc in enumerate(deployment_order, 1):
            lines.append(f"{i}. {svc}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# ReAct agent singleton
# ─────────────────────────────────────────────────────────────────────────────

AGENT_01_TOOLS = [
    search_provider_docs,
    find_equivalent_service,
    get_pricing,
    lookup_terraform_mapping,
    check_service_maturity,
    score_service_candidates,
    decide_7r_strategy,
    decide_7r_strategy_v2,
]

_agent_01      = None
_agent_01_lock = threading.Lock()


def _get_agent_01():
    """Return the cached ReAct agent, creating it on first call."""
    global _agent_01
    with _agent_01_lock:
        if _agent_01 is None:
            _agent_01 = create_react_agent(
                _get_llm(), AGENT_01_TOOLS, prompt=AGENT_01_SYSTEM
            )
    return _agent_01


# ─────────────────────────────────────────────────────────────────────────────
# LangGraph node — run_agent_01
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_01(state: MigrationState) -> dict:
    """LangGraph node — Agent 01: cloud migration planner.

    Reads:  state["dependency_graph"]
    Writes: {"migration_plan": dict, "artifacts": {"preview_report": str}}

    Steps:
      1. Extract graph nodes + build migration context from state.
      2. Batch nodes by category.
      3. Run the ReAct loop per batch (max 3 iterations each).
      4. Consolidate results: compute summary, deployment order, timeline.
      5. Validate and clean the final plan.
      6. Generate the markdown preview report.
    """
    logger.info("Agent01 — starting")

    dependency_graph = state.get("dependency_graph", {})
    target_cloud     = (state.get("target_cloud") or "gcp").lower()
    source_cloud     = (
        dependency_graph.get("detected_cloud")
        or state.get("source_cloud")
        or "aws"
    ).lower()

    budget_usd      = state.get("monthly_budget_usd")
    budget_eur      = round(float(budget_usd) * 0.92, 2) if budget_usd else None
    target_region   = state.get("target_region", "")
    data_residency  = state.get("data_residency_requirement", "")
    is_production   = bool(state.get("is_production", True))

    migration_context = {
        "cloud_source":   source_cloud,
        "cloud_target":   target_cloud,
        "budget_max":     budget_eur,
        "target_region":  target_region,
        "data_residency": data_residency,
        "production":     is_production,
    }

    # Extract nodes from the dependency graph
    resources_data = dependency_graph.get("resources", {})
    if isinstance(resources_data, dict):
        all_nodes = resources_data.get("nodes", [])
        all_edges = resources_data.get("edges", [])
    else:
        all_nodes = list(resources_data) if resources_data else []
        all_edges = []

    # State pre-validation
    state_errors: list[str] = []
    if not target_cloud:
        state_errors.append("Missing 'target_cloud'")
    if target_cloud not in {"aws", "gcp", "azure"}:
        state_errors.append(f"Invalid target_cloud: '{target_cloud}'")

    if state_errors:
        logger.error(f"Agent01: invalid state — {state_errors}")
        error_plan = {
            "migration_context": migration_context,
            "services": [], "resources": [], "deployment_order": [], "summary": {},
            "note": f"State validation failed: {'; '.join(state_errors)}",
        }
        return {
            "migration_plan": error_plan,
            "artifacts": {
                **(state.get("artifacts") or {}),
                "preview_report": f"State validation failed: {'; '.join(state_errors)}",
            },
        }

    # No nodes detected
    if not all_nodes:
        files = dependency_graph.get("files_analyzed", 0)
        note  = (
            f"Aucune ressource cloud détectée ({files} fichier(s) analysé(s)). "
            "Le dépôt ne semble pas utiliser de services cloud identifiables."
        )
        logger.warning(f"Agent01: {note}")
        empty_plan = {
            "migration_context": migration_context,
            "services": [], "resources": [], "deployment_order": [],
            "summary": {
                "total_services": 0, "total_effort_days": 0,
                "total_monthly_eur": 0.0, "budget_respected": True,
                "strategies_count": {},
            },
            "note": note,
        }
        try:
            preview = _gen_preview_report(
                migration_plan_json=json.dumps(empty_plan),
                source_cloud=source_cloud,
                target_cloud=target_cloud,
                framework=dependency_graph.get("framework", ""),
                vector_db=dependency_graph.get("vector_db", ""),
            )
        except Exception:
            preview = f"## Plan de migration\n\n⚠️ {note}"
        return {
            "migration_plan": empty_plan,
            "artifacts": {**(state.get("artifacts") or {}), "preview_report": preview},
        }

    # Phase 1: group nodes by source cloud (handles multi-cloud source architectures).
    # When detected_cloud == "multi", each node carries its own "cloud" attribute;
    # we process each provider group independently so the ReAct agent always receives
    # a homogeneous source_cloud for its lookup_terraform_mapping calls.
    if source_cloud == "multi":
        cloud_dist = dependency_graph.get("cloud_distribution", {})
        nodes_by_cloud: dict[str, list[dict]] = {}
        for node in all_nodes:
            node_cloud = node.get("cloud", "")
            if not node_cloud or node_cloud == "unknown":
                # Assign untagged nodes to the majority provider as a safe fallback
                node_cloud = max(cloud_dist, key=cloud_dist.get) if cloud_dist else "aws"
            nodes_by_cloud.setdefault(node_cloud, []).append(node)
        logger.info(
            f"Agent01 [multi-cloud source]: "
            + ", ".join(f"{c}={len(n)} node(s)" for c, n in nodes_by_cloud.items())
        )
    else:
        nodes_by_cloud = {source_cloud: all_nodes}

    # Phase 2: for each source cloud group, batch by category then run ReAct loop.
    react_agent  = _get_agent_01()
    all_services: list[dict] = []

    for cloud_src, cloud_nodes in nodes_by_cloud.items():
        # Pre-filter: nodes tagged is_external_connection=true are connection strings
        # to external services the app does NOT own (e.g. RDS_URL pointing to an
        # existing AWS RDS database). Assigning RETAIN deterministically here avoids
        # sending them to the ReAct LLM loop where they would be wrongly mapped to
        # new cloud resources.
        # Service IDs that are ALWAYS external connection strings when their node
        # was detected exclusively from env_var_pattern or manifest (DB driver).
        # These are never owned resources when detected this way — they're just
        # connection targets. If the user explicitly marks owns_database=True in
        # the wizard, the node source becomes "user_selection" and is NOT filtered.
        _EXTERNAL_SERVICE_IDS: frozenset[str] = frozenset({
            "rds", "elasticache", "documentdb", "opensearch",
            "postgresql", "mysql", "mongodb", "redis",
            "msk", "kafka", "elasticsearch",
        })
        # Sources that produce connectivity-only signals (no IaC → no ownership proof)
        _CONNECTIVITY_ONLY_SOURCES: frozenset[str] = frozenset({
            "env_var_pattern", "manifest",
        })

        # Apply user ownership confirmations from the wizard.
        # For each repo URL in owned_repos, nodes whose source_file starts with
        # that URL get owns_resource=True, bypassing the external-connection filter.
        _owned_repos: list[str] = list(state.get("owned_repos") or [])
        if _owned_repos:
            for _n in cloud_nodes:
                _sf = (_n.get("source_file") or "").strip()
                if not _n.get("owns_resource") and _sf:
                    for _or in _owned_repos:
                        _or_norm = _or.rstrip("/")
                        if _sf.startswith(_or_norm):
                            _n["owns_resource"] = True
                            break

        external_nodes: list[dict] = []
        plannable_nodes: list[dict] = []
        for _n in cloud_nodes:
            _hints = _n.get("contextual_hints") or {}
            _node_source = _n.get("source", "")
            _node_id = (_n.get("id") or _n.get("service_name", "")).lower()
            _user_confirmed_owner = bool(_n.get("owns_resource") or _n.get("user_confirmed_owner"))
            _is_external = (
                not _user_confirmed_owner
                and (
                    _hints.get("is_external_connection")
                    or (
                        _node_source in _CONNECTIVITY_ONLY_SOURCES
                        and _node_id in _EXTERNAL_SERVICE_IDS
                    )
                )
            )
            if _is_external:
                external_nodes.append(_n)
            else:
                plannable_nodes.append(_n)

        for _ext_node in external_nodes:
            _nid = _ext_node.get("id") or _ext_node.get("service_name", "unknown")
            _hint_msg = (_ext_node.get("contextual_hints") or {}).get("hint", "")
            all_services.append({
                "service_name":       _nid,
                "source_service":     _nid,
                "target_service":     None,
                "target_equivalent":  None,
                "terraform_resource": None,
                "strategy_7r":        "RETAIN",
                "strategy":           "RETAIN",
                "category":           _ext_node.get("type") or _ext_node.get("category") or "database",
                "type":               _ext_node.get("type") or "database",
                "equivalence_score":  1.0,
                "monthly_cost_eur":   0.0,
                "effort_days":        0,
                "budget_ok":          True,
                "cloud_confirmed":    False,
                "intent_valid":       True,
                "breaking_changes":   [],
                "reasoning": (
                    f"Node '{_nid}' is an external connection string env var — the application "
                    f"connects to this service but does not own it. No cloud resource should be "
                    f"created. Strategy: RETAIN (keep using the existing external endpoint). "
                    f"{_hint_msg}"
                ),
            })
            logger.info(
                "Agent01 [%s]: node '%s' tagged is_external_connection=true → RETAIN (skipping LLM)",
                cloud_src, _nid,
            )

        if not plannable_nodes:
            logger.info("Agent01 [%s]: all nodes are external connections — nothing to plan.", cloud_src)
            continue

        # Fast-path: nodes tagged contextual_hints.ai_service = "bedrock" (or any known
        # AI service) are resolved deterministically without calling the ReAct LLM.
        # This guarantees correct migration even when the Terraform resource type is
        # a verbose sub-resource like aws_bedrock_model_invocation_logging_configuration
        # that would otherwise confuse the generic LLM scoring.
        # Fast-path: AI services only. These are resolved deterministically because:
        # 1. AWS Bedrock/SageMaker have no direct Terraform resource on the source side
        #    (the resource type is a logging config, not the model itself), so RAG
        #    lookup_terraform_mapping returns low-similarity noise.
        # 2. The target mapping is unambiguous regardless of context.
        # All other services (rds, s3, iam, lambda, etc.) are now handled by the
        # LLM ReAct loop which receives contextual_hints injected by ask_user_services_node.
        _AI_FASTPATH: dict[str, dict] = {
            "bedrock": {
                "azure": ("azurerm_machine_learning_workspace", "Azure Machine Learning", 0.72),
                "gcp":   ("google_vertex_ai_endpoint",          "Vertex AI",              0.70),
            },
            "sagemaker": {
                "azure": ("azurerm_machine_learning_workspace", "Azure Machine Learning", 0.72),
                "gcp":   ("google_vertex_ai_endpoint",           "Vertex AI",              0.74),
            },
        }
        _tc_key = target_cloud.lower().replace("azurerm", "azure").replace("google", "gcp")
        _fastpath_resolved: list[dict] = []
        _remaining_plannable: list[dict] = []
        for _n in plannable_nodes:
            _hints = _n.get("contextual_hints") or {}
            _ai_svc = (_hints.get("ai_service") or "").lower()
            # Also resolve by node id/type for user_selection nodes (no contextual_hints)
            if not _ai_svc:
                _node_id_lower = (_n.get("id") or _n.get("service_name") or "").lower()
                if _node_id_lower in _AI_FASTPATH:
                    _ai_svc = _node_id_lower
            _fp = _AI_FASTPATH.get(_ai_svc, {}).get(_tc_key)
            if _fp:
                _tf_res, _target_name, _score = _fp
                _nid = _n.get("id") or _n.get("service_name") or _ai_svc
                all_services.append(_normalize_service({
                    "service_name":       _nid,
                    "source_service":     _nid,
                    "target_service":     _target_name,
                    "target_equivalent":  _target_name,
                    "terraform_resource": _tf_res,
                    "strategy_7r":        "REPLATFORM",
                    "strategy":           "REPLATFORM",
                    "category":           "ai",
                    "type":               "ai",
                    "equivalence_score":  _score,
                    "monthly_cost_eur":   0.0,
                    "effort_days":        3,
                    "budget_ok":          True,
                    "cloud_confirmed":    True,
                    "intent_valid":       True,
                    "breaking_changes":   [{
                        "pattern":     "AWS SDK (boto3 bedrock/sagemaker client)",
                        "replacement": "Target cloud SDK (e.g. azure-ai-ml / google-cloud-aiplatform)",
                        "severity":    "HIGH",
                    }],
                    "reasoning": (
                        f"IaC declares '{_nid}' (AWS managed AI service). "
                        f"Strategy: REPLATFORM → {_target_name} ({_tf_res}). "
                        f"{_hints.get('hint', '')}"
                    ),
                }, cloud_src, target_cloud))
                logger.info(
                    "Agent01 [%s]: node '%s' tagged ai_service=%s → REPLATFORM → %s (fast-path, no LLM)",
                    cloud_src, _nid, _ai_svc, _tf_res,
                )
            else:
                _remaining_plannable.append(_n)
        plannable_nodes = _remaining_plannable

        # Incremental mode: filter to only resources that changed since last migration
        _migration_mode = (state.get("migration_mode") or "full").lower()
        _incremental_resources = list(state.get("incremental_resources") or [])
        if _migration_mode == "incremental" and _incremental_resources:
            _inc_lower = [r.lower() for r in _incremental_resources]
            _filtered = [
                n for n in plannable_nodes
                if (n.get("id") or n.get("service_name", "")).lower() in _inc_lower
                or (n.get("type") or "").lower() in _inc_lower
            ]
            if _filtered:
                logger.info(
                    "Agent01 [%s]: incremental mode — filtered %d → %d nodes (resources: %s)",
                    cloud_src, len(plannable_nodes), len(_filtered), _incremental_resources,
                )
                plannable_nodes = _filtered
            else:
                logger.info(
                    "Agent01 [%s]: incremental filter matched 0 nodes — keeping all %d "
                    "(incremental_resources=%s may not match node ids/types)",
                    cloud_src, len(plannable_nodes), _incremental_resources,
                )

        if not plannable_nodes and not _fastpath_resolved:
            logger.info("Agent01 [%s]: all nodes resolved (fast-path or external).", cloud_src)
            continue

        batches = _batch_by_category(plannable_nodes)
        # Build a per-cloud migration context so the LLM uses the correct source provider
        cloud_migration_context = {**migration_context, "cloud_source": cloud_src}
        logger.info(
            f"Agent01 [{cloud_src}→{target_cloud}]: {len(plannable_nodes)} node(s) → "
            f"{len(batches)} batch(es): {list(batches.keys())}"
        )
        for category, batch_nodes in batches.items():
            node_ids    = {n.get("id") or n.get("service_name", "") for n in batch_nodes}
            batch_edges = [
                e for e in all_edges
                if e.get("source") in node_ids or e.get("target") in node_ids
            ]
            batch_services = _run_batch_with_react_loop(
                batch_nodes=batch_nodes,
                batch_edges=batch_edges,
                category=category,
                react_agent=react_agent,
                source_cloud=cloud_src,
                target_cloud=target_cloud,
                migration_context=cloud_migration_context,
                state=state,
            )
            all_services.extend(batch_services)

    # Phase 2b: AI Stack enrichment.
    # For EVERY AI component detected in the source repo (LLM provider, vector DB,
    # embedding model, ML framework), add the corresponding managed cloud service
    # as a migration target so Agent 02 generates the matching Terraform resource.
    #
    # Detection signal: dependency_graph["python_ai_stack"] produced by StackAnalyzer.
    # Keys used: llm_provider, llm_framework, vector_store, embedding_model,
    #            providers_detected (list of class/module names).

    # ── Mapping tables: detected keyword → (terraform_resource, category, reasoning) ──
    # Each entry: (signal_keyword, [target_cloud, ...], terraform_resource, category,
    #              equivalence_score, effort_days, monthly_cost_eur, sdk_note)
    _AI_COMPONENT_MAP: list[tuple] = [
        # ──────────────── LLM providers ──────────────────────────────────────
        # (keyword, clouds, tf_resource, category, score, effort, cost, sdk_note)
        ("openai",           ["azure"],       "azurerm_cognitive_account",          "ai",       0.88, 2, 0.0,
         "Replace OPENAI_API_KEY+base_url with AZURE_OPENAI_API_KEY+AZURE_OPENAI_ENDPOINT. "
         "Use AzureOpenAI() client. Set api_version='2024-02-01'."),
        ("openai",           ["gcp"],         "google_vertex_ai_endpoint",          "ai",       0.82, 3, 0.0,
         "Port to Vertex AI (google-generativeai SDK). Use VertexAI() LangChain integration."),
        ("openai",           ["aws"],         "aws_bedrock_model_invocation_logging_configuration", "ai", 0.80, 3, 0.0,
         "Use Amazon Bedrock (Claude, Titan). Replace openai.ChatCompletion with boto3 bedrock-runtime."),
        ("anthropic",        ["azure"],       "azurerm_cognitive_account",          "ai",       0.75, 3, 0.0,
         "No Claude on Azure; use Azure OpenAI GPT-4o as nearest equivalent. "
         "Replace anthropic.Anthropic() with AzureOpenAI(). Review prompt formatting differences."),
        ("anthropic",        ["gcp"],         "google_vertex_ai_endpoint",          "ai",       0.90, 2, 0.0,
         "Anthropic Claude available via Vertex AI Model Garden. Use google-cloud-aiplatform SDK."),
        ("anthropic",        ["aws"],         "aws_bedrock_model_invocation_logging_configuration", "ai", 0.92, 1, 0.0,
         "Anthropic Claude natively available on Amazon Bedrock. Use boto3 bedrock-runtime client."),
        ("cohere",           ["azure"],       "azurerm_cognitive_account",          "ai",       0.70, 3, 0.0,
         "Cohere not on Azure; use Azure OpenAI embeddings as alternative. "
         "Review task for retrieval vs generation use-case."),
        ("cohere",           ["aws"],         "aws_bedrock_model_invocation_logging_configuration", "ai", 0.85, 2, 0.0,
         "Cohere Command/Embed available on Amazon Bedrock. Replace cohere.Client() with boto3."),
        ("mistral",          ["azure"],       "azurerm_cognitive_account",          "ai",       0.78, 3, 0.0,
         "Mistral available via Azure AI Foundry (azurerm_cognitive_account kind=MistralAI)."),
        ("mistral",          ["gcp"],         "google_vertex_ai_endpoint",          "ai",       0.80, 2, 0.0,
         "Mistral available via Vertex AI. Use google-cloud-aiplatform Model Garden."),
        ("huggingface",      ["azure"],       "azurerm_machine_learning_workspace", "ai",       0.72, 5, 80.0,
         "Deploy HuggingFace models via Azure Machine Learning inference endpoints. "
         "Use azure-ai-ml SDK. Set AZURE_ML_ENDPOINT instead of HF_ENDPOINT."),
        ("huggingface",      ["gcp"],         "google_vertex_ai_endpoint",          "ai",       0.74, 4, 60.0,
         "Deploy via Vertex AI custom prediction. Upload model to GCS, register in Vertex Model Registry."),
        ("huggingface",      ["aws"],         "aws_sagemaker_endpoint",             "ai",       0.76, 4, 60.0,
         "Deploy via SageMaker HuggingFace containers. Use sagemaker.HuggingFaceModel()."),
        ("llama",            ["azure"],       "azurerm_machine_learning_workspace", "ai",       0.70, 5, 80.0,
         "Deploy Llama via Azure Machine Learning. Available as managed endpoint in azurerm_ml_workspace."),
        ("llama",            ["aws"],         "aws_sagemaker_endpoint",             "ai",       0.75, 4, 60.0,
         "Deploy Llama via SageMaker JumpStart. Available as SageMaker foundation model."),
        ("bedrock",          ["azure"],       "azurerm_machine_learning_workspace", "ai",       0.72, 3, 80.0,
         "AWS Bedrock → Azure Machine Learning managed endpoints (azurerm_machine_learning_workspace). "
         "Avoids Azure OpenAI/Cognitive Services quota restrictions on restricted subscriptions. "
         "Deploy via azure-ai-ml SDK; replace boto3 bedrock-runtime calls with an MLClient online-endpoint invocation."),

        # ──────────────── Vector / embedding stores ───────────────────────────
        ("chromadb",         ["azure"],       "azurerm_search_service",             "vector_db", 0.80, 3, 80.0,
         "ChromaDB → Azure AI Search (azurerm_search_service). "
         "Replace chromadb.Client() with azure.search.documents SearchClient. "
         "Migrate index schema to Azure AI Search index definition."),
        ("chromadb",         ["gcp"],         "google_vertex_ai_vector_store",      "vector_db", 0.78, 3, 60.0,
         "ChromaDB → Vertex AI Vector Search. Use aiplatform.MatchingEngineIndex."),
        ("chromadb",         ["aws"],         "aws_opensearchserverless_collection","vector_db", 0.75, 4, 40.0,
         "ChromaDB → OpenSearch Serverless (k-NN plugin). Use boto3 opensearchservice client."),
        ("pinecone",         ["azure"],       "azurerm_search_service",             "vector_db", 0.82, 2, 80.0,
         "Pinecone → Azure AI Search (vector search mode). "
         "Replace pinecone.Index() with SearchClient. Migrate namespace → index in Azure."),
        ("pinecone",         ["gcp"],         "google_vertex_ai_vector_store",      "vector_db", 0.85, 2, 60.0,
         "Pinecone → Vertex AI Vector Search. Replace pinecone.index().upsert() with aiplatform calls."),
        ("pinecone",         ["aws"],         "aws_opensearchserverless_collection","vector_db", 0.80, 3, 40.0,
         "Pinecone → OpenSearch Serverless. Replace pinecone SDK with opensearch-py client."),
        ("qdrant",           ["azure"],       "azurerm_search_service",             "vector_db", 0.78, 3, 80.0,
         "Qdrant → Azure AI Search. Replace qdrant_client with Azure SearchClient."),
        ("qdrant",           ["gcp"],         "google_vertex_ai_vector_store",      "vector_db", 0.80, 3, 60.0,
         "Qdrant → Vertex AI Vector Search. Migrate collection schema to index definition."),
        ("qdrant",           ["aws"],         "aws_opensearchserverless_collection","vector_db", 0.76, 3, 40.0,
         "Qdrant → OpenSearch Serverless k-NN. Replace qdrant_client with opensearch-py."),
        ("weaviate",         ["azure"],       "azurerm_search_service",             "vector_db", 0.76, 3, 80.0,
         "Weaviate → Azure AI Search. Replace weaviate.Client() with SearchClient."),
        ("weaviate",         ["aws"],         "aws_opensearchserverless_collection","vector_db", 0.74, 4, 40.0,
         "Weaviate → OpenSearch Serverless. Migrate schema to OS index mapping."),
        ("milvus",           ["azure"],       "azurerm_search_service",             "vector_db", 0.74, 4, 80.0,
         "Milvus → Azure AI Search. Replace pymilvus with azure-search-documents."),
        ("milvus",           ["aws"],         "aws_opensearchserverless_collection","vector_db", 0.72, 4, 40.0,
         "Milvus → OpenSearch Serverless k-NN. Replace pymilvus with opensearch-py."),
        ("pgvector",         ["azure"],       "azurerm_postgresql_flexible_server", "vector_db", 0.95, 1, 53.0,
         "pgvector extension supported on Azure Database for PostgreSQL Flexible Server. "
         "Enable via CREATE EXTENSION vector; — no code change needed for the extension itself."),
        ("pgvector",         ["gcp"],         "google_alloydb_cluster",             "vector_db", 0.90, 2, 70.0,
         "pgvector supported on AlloyDB for PostgreSQL. Enable via CREATE EXTENSION vector."),
        ("pgvector",         ["aws"],         "aws_db_instance",                    "vector_db", 0.92, 1, 40.0,
         "pgvector supported on RDS PostgreSQL 14+. Enable via CREATE EXTENSION vector."),
        ("faiss",            ["azure"],       None,                                 "vector_db", 0.60, 2, 0.0,
         "FAISS is in-memory — no cloud resource needed. "
         "Consider migrating to Azure AI Search for persistence and scaling."),

        # ──────────────── ML platforms / model serving ────────────────────────
        ("sagemaker",        ["azure"],       "azurerm_machine_learning_workspace", "ai",       0.72, 5, 80.0,
         "SageMaker → Azure Machine Learning. Replace sagemaker SDK with azure-ai-ml."),
        ("sagemaker",        ["gcp"],         "google_vertex_ai_endpoint",          "ai",       0.74, 5, 70.0,
         "SageMaker → Vertex AI. Replace sagemaker Estimator with google-cloud-aiplatform CustomJob."),
        ("vertex_ai",        ["aws"],         "aws_sagemaker_endpoint",             "ai",       0.74, 5, 60.0,
         "Vertex AI → SageMaker. Replace aiplatform.Model with sagemaker.Model."),
        ("azure_ml",         ["aws"],         "aws_sagemaker_endpoint",             "ai",       0.72, 5, 60.0,
         "Azure ML → SageMaker. Replace azure-ai-ml with sagemaker SDK."),
    ]

    _ai_stack     = dependency_graph.get("python_ai_stack") or {}
    _raw_signals: list[str] = []
    for _key in ("providers_detected", "llm_provider", "llm_framework",
                 "vector_store", "embedding_model", "frameworks_detected"):
        _val = _ai_stack.get(_key)
        if isinstance(_val, list):
            _raw_signals.extend(str(v) for v in _val if v)
        elif _val:
            _raw_signals.append(str(_val))

    # Normalize: lower-case, strip spaces
    _signals = [s.lower().strip() for s in _raw_signals if s and s.strip()]

    # Determine effective target cloud key for lookup
    _tc = target_cloud.lower().replace("azurerm", "azure").replace("google", "gcp")

    _existing_targets = {
        s.get("target_service") or s.get("target_equivalent", "")
        for s in all_services
    }

    _added_tf_resources: set[str] = set()  # avoid adding same TF resource twice

    for _sig, _clouds, _tf_resource, _cat, _score, _effort, _cost, _sdk_note in _AI_COMPONENT_MAP:
        # Check if this signal matches any detected AI component
        if not any(_sig in _signal for _signal in _signals):
            continue
        # Check if this mapping targets the current cloud
        if _tc not in _clouds:
            continue
        # No Terraform resource needed (e.g. FAISS)
        if _tf_resource is None:
            logger.info(
                "Agent01: AI Stack — %s detected, no cloud resource needed for %s", _sig, _tc
            )
            continue
        # Avoid duplicates
        if _tf_resource in _existing_targets or _tf_resource in _added_tf_resources:
            continue

        _src_name = next(
            (s for s in _signals if _sig in s),
            _sig,
        )
        _svc_name = f"{_tf_resource.replace('azurerm_', '').replace('aws_', '').replace('google_', '')}"

        all_services.append({
            "service_name":       _svc_name,
            "source_service":     _src_name,
            "target_service":     _tf_resource,
            "target_equivalent":  _tf_resource,
            "terraform_resource": _tf_resource,
            "strategy_7r":        "REPLATFORM",
            "strategy":           "REPLATFORM",
            "category":           _cat,
            "type":               _cat,
            "equivalence_score":  _score,
            "monthly_cost_eur":   _cost,
            "effort_days":        _effort,
            "budget_ok":          True,
            "cloud_confirmed":    True,
            "intent_valid":       True,
            "breaking_changes":   [{"pattern": _src_name, "description": _sdk_note}],
            "sdk_changes":        {"source_import": _src_name, "target_import": _tf_resource,
                                   "breaking_changes": [{"pattern": _src_name, "description": _sdk_note}]},
            "reasoning": (
                f"AI Stack component '{_src_name}' detected in source repo. "
                f"Migrating to {_tc.upper()}: {_tf_resource} is the managed equivalent. "
                f"{_sdk_note[:200]}"
            ),
        })
        _added_tf_resources.add(_tf_resource)
        logger.info(
            "Agent01: AI Stack enrichment — added %s (%s→%s) for source=%s",
            _tf_resource, _src_name, _tc, _sig,
        )

    # Phase 2c: coverage diagnostic — warn for every graph node that has no
    # corresponding entry in the plan (helps catch silent batch failures and
    # overly-aggressive hallucination filtering).
    _planned_sources: set[str] = set()
    for _s in all_services:
        for _k in ("source_service", "service_name", "resource_name"):
            _v = _s.get(_k)
            if _v:
                _planned_sources.add(str(_v).strip().lower())

    _missing_nodes: list[str] = []
    for _node in all_nodes:
        _nid = (_node.get("id") or _node.get("service_name", "")).strip()
        if not _nid:
            continue
        _nid_low = _nid.lower()
        # Consider covered if the node id (or a close normalisation) is in planned sources.
        _covered = (
            _nid_low in _planned_sources
            or any(_nid_low in _ps or _ps in _nid_low for _ps in _planned_sources)
        )
        if not _covered:
            _missing_nodes.append(_nid)

    if _missing_nodes:
        logger.warning(
            "Agent01: %d graph node(s) have NO entry in the migration plan "
            "(likely batch failure or hallucination filter drop): %s",
            len(_missing_nodes), _missing_nodes,
        )
    else:
        logger.info(
            "Agent01: all %d graph node(s) are covered by the migration plan.",
            len(all_nodes),
        )

    # Phase 3: consolidate summary
    total_eur        = sum(s.get("monthly_cost_eur", 0.0) or 0.0 for s in all_services)
    total_effort     = sum(s.get("effort_days", 0) or 0 for s in all_services)
    per_service_ok   = all(s.get("budget_ok", True) for s in all_services)
    total_within_budget = (budget_eur is None) or (total_eur <= budget_eur)
    budget_respected = per_service_ok and total_within_budget
    if not total_within_budget and budget_eur is not None:
        logger.warning(
            "Agent01: total cost %.2f EUR/month exceeds budget %.2f EUR (≈$%.0f) — "
            "consider downsizing services",
            total_eur, budget_eur, budget_usd,
        )

    strategies: dict[str, int] = {}
    for s in all_services:
        strat = s.get("strategy_7r", "REPLATFORM").upper()
        strategies[strat] = strategies.get(strat, 0) + 1

    savings_pct, optimized_monthly_eur = _compute_savings(all_services)

    # Default layer order — used as tiebreaker when no dependency edges exist.
    # database(4) before storage(5): data layer must exist before file storage
    # is populated or referenced by the application.
    _DEPLOY_PRIORITY: dict[str, int] = {
        "iam": 1, "security": 2, "network": 3, "networking": 3,
        "database": 4, "storage": 5, "messaging": 6,
        "compute": 7, "monitoring": 8, "search": 9,
        "ai": 10, "vector_db": 11, "framework": 12, "unknown": 13, "other": 14,
    }

    # Build service-id → category map (from graph nodes + planned services).
    _svc_category: dict[str, str] = {}
    for _node in all_nodes:
        _nid = _node.get("id") or _node.get("service_name", "")
        if _nid:
            _svc_category[_nid] = _node.get("type") or _node.get("category") or "other"
    for _s in all_services:
        for _k in ("service_name", "source_service", "resource_name"):
            _sid = _s.get(_k, "")
            if _sid:
                _svc_category[_sid] = _s.get("category") or _svc_category.get(_sid, "other")

    _cats_set: set[str] = {s.get("category", "other") for s in all_services}

    # Build category-level dependency graph from detected service edges.
    # edge: source depends-on/calls target → target must be deployed before source.
    _cat_deps: dict[str, set[str]] = {c: set() for c in _cats_set}
    for _edge in all_edges:
        _src_id = _edge.get("source") or _edge.get("from", "")
        _tgt_id = _edge.get("target") or _edge.get("to", "")
        _src_cat = _svc_category.get(_src_id, "")
        _tgt_cat = _svc_category.get(_tgt_id, "")
        # An edge src→tgt means src uses tgt, so tgt must deploy first.
        if _src_cat in _cat_deps and _tgt_cat in _cat_deps and _src_cat != _tgt_cat:
            _cat_deps[_src_cat].add(_tgt_cat)

    # Kahn's topological sort with _DEPLOY_PRIORITY as tiebreaker.
    _in_deg: dict[str, int] = {c: 0 for c in _cats_set}
    _rev: dict[str, list[str]] = {c: [] for c in _cats_set}
    for _c, _deps in _cat_deps.items():
        for _dep in _deps:
            if _dep in _in_deg:
                _in_deg[_c] += 1
                _rev[_dep].append(_c)

    _ready = sorted(
        (_c for _c, _d in _in_deg.items() if _d == 0),
        key=lambda c: _DEPLOY_PRIORITY.get(c, 99),
    )
    _ordered: list[str] = []
    while _ready:
        _cur = _ready.pop(0)
        _ordered.append(_cur)
        for _nb in _rev.get(_cur, []):
            _in_deg[_nb] -= 1
            if _in_deg[_nb] == 0:
                _ready.append(_nb)
        _ready.sort(key=lambda c: _DEPLOY_PRIORITY.get(c, 99))

    # Append any remaining categories (cycles or unmapped) sorted by priority.
    _ordered.extend(
        sorted(
            (_c for _c in _cats_set if _c not in _ordered),
            key=lambda c: _DEPLOY_PRIORITY.get(c, 99),
        )
    )
    categories_present = _ordered

    if all_edges:
        logger.info(
            "Agent01: deployment order built from %d dependency edge(s): %s",
            len(all_edges), categories_present,
        )
    else:
        logger.info(
            "Agent01: deployment order built from static priority (no edges): %s",
            categories_present,
        )

    strategies_count = {k.lower(): v for k, v in strategies.items()}

    migration_plan: dict = {
        "migration_context": migration_context,
        "services":         all_services,
        "resources":        all_services,   # Agent 02/03 compat (same list)
        "deployment_order": categories_present,
        "summary": {
            "total_services":              len(all_services),
            "total_effort_days":           total_effort,
            "total_monthly_eur":           round(total_eur, 2),
            "budget_respected":            budget_respected,
            "strategies_count":            strategies_count,
            "confirmed_services":          len(all_services),
            "estimated_total_monthly":     round(total_eur, 2),
            "estimated_optimized_monthly": optimized_monthly_eur,
            "potential_savings_pct":       savings_pct,
            "strategies_breakdown":        strategies,
            "migration_complexity":        _assess_complexity(strategies),
        },
    }

    # Phase 3b: final validation + cleanup
    val_errors = _validate_plan_inline(migration_plan)
    if val_errors:
        logger.error("Agent01: final plan invalid:\n" + "\n".join(f"  - {e}" for e in val_errors))
        clean = [
            s for s in all_services
            if all(f in s for f in ["service_name", "source_service", "strategy_7r"])
            and s.get("strategy_7r", "").upper() in _VALID_STRATEGIES
        ]
        migration_plan["services"]  = clean
        migration_plan["resources"] = clean
        logger.info(f"Agent01: plan cleaned — {len(all_services)} → {len(clean)} services")
        validation_status = "CLEANED_UP"
    else:
        logger.info(f"Agent01: plan valid — {len(all_services)} services")
        validation_status = "PASSED"

    # Phase 3c: semantic intent validation — detect source↔target category mismatches
    intent_mismatches = []
    try:
        intent_mismatches = validate_migration_intent(migration_plan)
        if intent_mismatches:
            logger.warning(
                "Agent01: %d intent mismatch(es) detected — annotating plan services",
                len(intent_mismatches),
            )
            mismatch_index = {m.service_name: m for m in intent_mismatches}
            for svc in migration_plan.get("services", []):
                name = svc.get("service_name") or svc.get("resource_name") or ""
                if name in mismatch_index:
                    m = mismatch_index[name]
                    svc["intent_valid"]    = False
                    svc["intent_mismatch"] = m.message
                    svc["intent_severity"] = m.severity
                else:
                    svc.setdefault("intent_valid", True)
        else:
            logger.info("Agent01: ✓ all intent checks passed — no source↔target mismatches")
            for svc in migration_plan.get("services", []):
                svc.setdefault("intent_valid", True)
    except Exception as exc:
        logger.warning("Agent01: intent validation skipped: %s", exc)

    # Phase 3d: audit log — persist decisions to migration_audit table
    migration_id = state.get("migration_id") or state.get("job_id") or "unknown"
    try:
        n_logged = log_migration_decisions(migration_id, migration_plan)
        if n_logged:
            planning_errors = detect_planning_errors(migration_id)
            if planning_errors:
                logger.warning(
                    "Agent01 [Audit]: %d suspicious decision(s) flagged post-hoc for migration=%s",
                    len(planning_errors), migration_id,
                )
    except Exception as exc:
        logger.warning("Agent01: audit logging skipped: %s", exc)

    # Phase 4: preview report
    try:
        preview = _gen_preview_report(
            migration_plan_json=json.dumps(migration_plan),
            source_cloud=source_cloud,
            target_cloud=target_cloud,
            framework=dependency_graph.get("framework") or "",
            vector_db=dependency_graph.get("vector_db") or "",
        )
    except Exception as exc:
        logger.warning(f"gen_preview_report failed (non-blocking): {exc}")
        preview = (
            f"# Plan de migration {source_cloud.upper()} → {target_cloud.upper()}\n\n"
            f"{len(migration_plan.get('services', []))} services planifiés."
        )

    logger.info(
        f"Agent01 — done: {len(migration_plan.get('services', []))} services | "
        f"total cost: {round(total_eur, 2)} EUR/month | "
        f"validation: {'✅ PASSED' if validation_status == 'PASSED' else '⚠️ ' + validation_status}"
    )

    return {
        "migration_plan": migration_plan,
        "artifacts": {
            **(state.get("artifacts") or {}),
            "preview_report":    preview,
            "validation_status": validation_status,
            "intent_mismatches": mismatches_to_issues(intent_mismatches),
        },
        "warnings": list(state.get("warnings") or []) + [
            f"[IntentValidator] {m.message}"
            for m in intent_mismatches
            if m.severity == "ERROR"
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Correction mode — re-plan only user-rejected services
# ─────────────────────────────────────────────────────────────────────────────

def run_agent_01_correction(
    rejected_services:    list[dict],
    rejection_reasons:    dict[str, str],
    accepted_plan:        dict,
    original_constraints: dict,
    state:                dict,
) -> dict:
    """Re-run Agent 01 only for services rejected by the user.

    Args:
        rejected_services:    List of rejected service dicts.
        rejection_reasons:    Mapping source_service → rejection reason string.
        accepted_plan:        The original migration_plan with accepted services intact.
        original_constraints: Constraints from the original run (budget, timeline, etc.).
        state:                Full LangGraph state.

    Returns:
        {"migration_plan": dict (rejected services replaced), "correction_status": str}
    """
    logger.info(f"Agent01 Correction: re-planning {len(rejected_services)} rejected service(s)")

    if not rejected_services:
        return {"migration_plan": accepted_plan, "correction_status": "no_rejections"}

    dependency_graph = state.get("dependency_graph", {})
    source_cloud     = (dependency_graph.get("detected_cloud") or state.get("source_cloud") or "aws").lower()
    target_cloud     = (state.get("target_cloud") or "gcp").lower()

    budget_usd   = (state.get("monthly_budget_usd")
                    or original_constraints.get("monthly_budget_usd"))
    budget_eur   = round(float(budget_usd) * 0.92, 2) if budget_usd else None

    migration_context = {
        "cloud_source":   source_cloud,
        "cloud_target":   target_cloud,
        "budget_max":     budget_eur,
        "target_region":  state.get("target_region", ""),
        "data_residency": state.get("data_residency_requirement", ""),
    }

    correction_nodes = [
        {
            "id":             svc.get("source_service", "unknown"),
            "service_name":   svc.get("source_service"),
            "type":           svc.get("category", "unknown"),
            "score":          3,
            "cloud_confirmed": svc.get("cloud_confirmed", True),
            "code_patterns":  svc.get("code_patterns", {}),
        }
        for svc in rejected_services
    ]

    excluded: list[str] = list(state.get("excluded_services") or [])
    for svc in rejected_services:
        t = svc.get("target_service") or svc.get("target_equivalent", "")
        if t and t not in excluded:
            excluded.append(t)

    feedback_ctx: dict = {
        "original_selections": [
            {
                "service":          svc.get("source_service"),
                "selected_target":  svc.get("target_service"),
                "strategy":         svc.get("strategy_7r", svc.get("strategy")),
                "rejection_reason": rejection_reasons.get(svc.get("source_service", ""), "user_rejection"),
            }
            for svc in rejected_services
        ],
        "request": (
            "Trouve des alternatives de migration pour ces services rejetés. "
            + (
                "\n\nSERVICES EXCLUS (ne pas re-proposer) :\n"
                + "\n".join(f"  - {s}" for s in excluded)
                if excluded else ""
            )
        ),
        "excluded_targets": excluded,
    }

    correction_state                      = dict(state)
    correction_state["_correction_context"] = feedback_ctx

    react_agent = _get_agent_01()
    try:
        corrected_services = _run_batch_with_react_loop(
            batch_nodes=correction_nodes,
            batch_edges=[],
            category="correction_batch",
            react_agent=react_agent,
            source_cloud=source_cloud,
            target_cloud=target_cloud,
            migration_context=migration_context,
            state=correction_state,
        )
    except Exception as exc:
        logger.error(f"Agent01 Correction: failed — {exc}. Keeping original plan.")
        return {"migration_plan": accepted_plan, "correction_status": "correction_failed"}

    # Merge: accepted services + corrected replacements.
    # If the correction loop returned nothing (LLM failure / 3 iterations exhausted),
    # fall back to keeping the original rejected services unchanged so they are never
    # silently dropped from the plan.
    rejected_sources = {svc.get("source_service") for svc in rejected_services}
    accepted_list    = [
        r for r in accepted_plan.get("resources", accepted_plan.get("services", []))
        if r.get("source_service") not in rejected_sources
    ]

    if not corrected_services:
        logger.warning(
            "Agent01 Correction: correction loop returned no services — "
            "keeping original rejected services in plan (no data loss)."
        )
        corrected_services = [
            r for r in accepted_plan.get("resources", accepted_plan.get("services", []))
            if r.get("source_service") in rejected_sources
        ]

    merged = list(accepted_list) + list(corrected_services)

    total_eur    = sum(s.get("monthly_cost_eur", s.get("monthly_cost_estimate", 0.0)) or 0.0 for s in merged)
    total_effort = sum(s.get("effort_days", 0) or 0 for s in merged)
    strategies: dict[str, int] = {}
    for s in merged:
        strat = s.get("strategy_7r", s.get("strategy", "REPLATFORM")).upper()
        strategies[strat] = strategies.get(strat, 0) + 1

    corr_savings_pct, corr_optimized_eur = _compute_savings(merged)

    corrected_plan: dict = {
        "migration_context": accepted_plan.get("migration_context", migration_context),
        "services":         merged,
        "resources":        merged,
        "deployment_order": accepted_plan.get("deployment_order", []),
        "summary": {
            "total_services":              len(merged),
            "total_effort_days":           total_effort,
            "total_monthly_eur":           round(total_eur, 2),
            "budget_respected":            (
                all(s.get("budget_ok", True) for s in merged)
                and (budget_eur is None or total_eur <= budget_eur)
            ),
            "strategies_count":            {k.lower(): v for k, v in strategies.items()},
            "confirmed_services":          len(merged),
            "estimated_total_monthly":     round(total_eur, 2),
            "estimated_optimized_monthly": corr_optimized_eur,
            "potential_savings_pct":       corr_savings_pct,
            "strategies_breakdown":        strategies,
            "migration_complexity":        _assess_complexity(strategies),
            "estimated_migration_weeks":   _compute_dynamic_weeks(merged),
        },
    }

    logger.info(
        f"Agent01 Correction: {len(rejected_services)} rejected → "
        f"{len(corrected_services)} new proposal(s)"
    )

    return {
        "migration_plan":     corrected_plan,
        "correction_status":  "completed",
        "corrections_applied": len(corrected_services),
    }
