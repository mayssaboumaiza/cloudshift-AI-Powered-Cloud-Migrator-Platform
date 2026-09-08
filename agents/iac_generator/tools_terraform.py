"""
tools_terraform.py — Agent 02/03/04/05 tools re-export facade.

This module preserves all import paths that existed before the file was split.
All logic has been moved to focused single-responsibility modules:

  hcl_merge.py         — HCL brace/string-aware merge + write_terraform_file  (Agent 02)
  react_tools.py       — validate_terraform_block, get_rag_context_for_resource,
                         read_generated_files                                   (Agent 02)
  reporting_tools.py   — gen_deploy_report, gen_rollback_report                (Agent 05)
  cloud_integration.py — CloudTarget MCP wrappers + render_deploy_template +
                         _fetch_vault_credentials + execute_terraform_apply     (Agent 03/04)
  cicd_tools.py        — generate_cicd_pipeline                                (Agent 03)

Scoring tools (score_candidates, compute_savings_opportunities) and the Jinja2
template tool (render_jinja2_template) are defined below — they are small and
have no better home yet.

New code should import directly from those modules.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger("Tools")

# ── Optional MCP client imports ───────────────────────────────────────────────

try:
    from agents.iac_generator.mcp.mcp_client import get_github_client, get_cloud_target_client
    get_cloud_source_client = None
except ImportError:
    get_github_client       = None  # type: ignore[assignment]
    get_cloud_target_client = None  # type: ignore[assignment]
    get_cloud_source_client = None  # type: ignore[assignment]

# ── Path resolution ───────────────────────────────────────────────────────────

_AGENT_02_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR   = os.path.dirname(_AGENT_02_DIR)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_AGENTS_DIR))
TEMPLATES_DIR = os.path.join(_AGENTS_DIR, "deployer", "templates")


def _get_output_dir() -> str:
    """Return current output directory, re-reading MIGRATION_OUTPUT_DIR each call."""
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    return _out_env if os.path.isabs(_out_env) else os.path.join(
        _PROJECT_ROOT, _out_env or os.path.join("output", "migrated_app")
    )


OUTPUT_DIR = _get_output_dir()

# ── Shared utilities ──────────────────────────────────────────────────────────

def _normalize_provider_name(provider: str) -> str:
    """Normalize provider names to Terraform registry identifiers."""
    p = (provider or "").strip().lower()
    return {"azure": "azurerm", "gcp": "google"}.get(p, p)


def _safe_dict(value: Any) -> dict:
    """Normalise a value to dict (handles JSON strings and native dicts)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _load_yaml(path: str) -> dict:
    """Load a YAML file, returning empty dict on any error."""
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.debug(f"YAML mapping not found: {path}")
        return {}
    except Exception as e:
        logger.warning(f"Failed to load YAML {path}: {e}")
        return {}


def _compute_weights(constraints: dict) -> dict:
    """Compute scoring weights based on user constraints."""
    budget   = (constraints.get("budget") or "moderate").lower()
    timeline = (constraints.get("timeline") or "3_months").lower()
    risk     = (constraints.get("risk_tolerance") or "medium").lower()

    if budget == "strict":
        return {"cost": 0.50, "performance": 0.15, "effort": 0.20, "modernity": 0.15}
    if timeline == "asap":
        return {"cost": 0.20, "performance": 0.20, "effort": 0.45, "modernity": 0.15}
    if risk == "low":
        return {"cost": 0.25, "performance": 0.25, "effort": 0.35, "modernity": 0.15}
    return {"cost": 0.30, "performance": 0.25, "effort": 0.25, "modernity": 0.20}


_EMBEDDING_MODELS_DB: dict[str, dict] = {
    "amazon.titan-embed-text-v1":    {"dims": 1536, "type": "proprietary", "cloud": "aws"},
    "amazon.titan-embed-text-v2:0":  {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "cohere.embed-english-v3":       {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "cohere.embed-multilingual-v3":  {"dims": 1024, "type": "proprietary", "cloud": "aws"},
    "textembedding-gecko":           {"dims": 768,  "type": "proprietary", "cloud": "gcp"},
    "text-embedding-004":            {"dims": 768,  "type": "proprietary", "cloud": "gcp"},
    "text-embedding-ada-002":        {"dims": 1536, "type": "proprietary", "cloud": "openai"},
    "text-embedding-3-small":        {"dims": 1536, "type": "proprietary", "cloud": "openai"},
    "text-embedding-3-large":        {"dims": 3072, "type": "proprietary", "cloud": "openai"},
    "all-MiniLM-L6-v2":              {"dims": 384,  "type": "open-source", "cloud": "none"},
    "bge-large-en-v1.5":             {"dims": 1024, "type": "open-source", "cloud": "none"},
    "bge-m3":                        {"dims": 1024, "type": "open-source", "cloud": "none"},
}

# ── Agent 02 scoring tools (kept here — small, no better home) ───────────────

@tool
def score_candidates(
    candidates: list,
    constraints: dict,
    pricing_data: dict,
) -> list:
    """Calcule le score composite pour chaque candidat de migration.

    Normalise les prix (0–100), intègre les scores d'effort 7R, de performance (SLA)
    et de modernité, puis applique les poids dérivés des contraintes utilisateur.

    Args:
        candidates  : Liste de dicts candidats (depuis lookup_mapping +
                      compare_service_generations). Chaque dict doit avoir au minimum
                      "service_name". Champs optionnels : "strategy", "sla_pct",
                      "modernity_score".
        constraints : {"budget": "strict|moderate|flexible",
                       "timeline": "ASAP|3_months|6_months|flexible",
                       "risk_tolerance": "low|medium|high"}
        pricing_data: Dict service_name → résultat de get_pricing_live
                      (clé "minimum_monthly" utilisée pour normalisation).

    Returns:
        Liste de candidats enrichis triés par composite_score décroissant.
        Chaque entrée ajoute : cost_score, performance_score, effort_score,
        modernity_score, composite_score, monthly_cost_estimate, pricing_source.
    """
    if not candidates:
        return []

    weights = _compute_weights(constraints)

    prices = []
    for c in candidates:
        name = c.get("service_name", "")
        p    = pricing_data.get(name, {})
        prices.append(float(p.get("minimum_monthly", 999) or 999))

    max_price   = max(prices) if prices else 1
    min_price   = min(prices) if prices else 0
    price_range = (max_price - min_price) or 1

    _EFFORT_MAP = {
        "REHOST": 90, "REPLATFORM": 70, "REPURCHASE": 60,
        "REFACTOR": 30, "RETIRE": 100, "RETAIN": 95, "RELOCATE": 85,
    }

    scored: list[dict] = []
    for i, candidate in enumerate(candidates):
        name  = candidate.get("service_name", "")
        price = prices[i]

        cost_score        = 100.0 * (1.0 - (price - min_price) / price_range)
        strategy          = (candidate.get("strategy") or "REPLATFORM").upper()
        effort_score      = float(_EFFORT_MAP.get(strategy, 60))
        sla               = float(candidate.get("sla_pct", 99.9) or 99.9)
        performance_score = min(100.0, max(0.0, (sla - 99.0) * 100.0))
        modernity_score   = float(candidate.get("modernity_score", 60) or 60)

        composite = (
            weights["cost"]        * cost_score +
            weights["performance"] * performance_score +
            weights["effort"]      * effort_score +
            weights["modernity"]   * modernity_score
        )

        pdata = pricing_data.get(name, {})
        scored.append({
            **candidate,
            "cost_score":            round(cost_score, 1),
            "performance_score":     round(performance_score, 1),
            "effort_score":          round(effort_score, 1),
            "modernity_score":       round(modernity_score, 1),
            "composite_score":       round(composite, 1),
            "monthly_cost_estimate": round(price, 2),
            "pricing_source":        pdata.get("source", "estimate"),
        })

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    return scored


@tool
def compute_savings_opportunities(
    service: str,
    cloud: str,
    pricing_data: dict,
    usage_profile: str = "medium",
) -> list:
    """Identifie les mécanismes de réduction de coût disponibles pour un service cible.

    Taux de réduction documentés par provider :
      AWS   : Reserved 1y = -30 %, Reserved 3y = -45 %, Savings Plans = -40 %,
              Spot = -70 % (interruptible seulement)
      GCP   : Committed 1y = -37 %, Committed 3y = -55 %, Sustained Auto = -30 %
      Azure : Reservation 1y = -36 %, Reservation 3y = -52 %, Hybrid Benefit = -40 %

    Args:
        service       : Nom du service cible (ex: "cloud-run", "cosmos-db")
        cloud         : "aws" | "gcp" | "azure"
        pricing_data  : Résultat de get_pricing_live pour ce service
                        (clé "minimum_monthly" utilisée comme base)
        usage_profile : "low" | "medium" | "high" (défaut: "medium")
                        Spot/Preemptible uniquement pour low/medium.

    Returns:
        Liste de dicts triés par saving_pct décroissant :
        {service, cloud, base_monthly, optimized_monthly, saving_pct, mechanism, condition}
    """
    _DISCOUNT_RATES: dict[str, dict[str, dict]] = {
        "aws": {
            "reserved_1y":   {"rate": 0.30, "condition": "1 year commitment, partial upfront"},
            "reserved_3y":   {"rate": 0.45, "condition": "3 year commitment, partial upfront"},
            "savings_plans": {"rate": 0.40, "condition": "Compute Savings Plans — flexible, applies to Lambda/EC2/Fargate"},
            "spot":          {"rate": 0.70, "condition": "Spot Instances — interruptible workloads only"},
        },
        "gcp": {
            "committed_1y":   {"rate": 0.37, "condition": "1 year committed use discount on vCPU and memory"},
            "committed_3y":   {"rate": 0.55, "condition": "3 year committed use discount"},
            "sustained_auto": {"rate": 0.30, "condition": "Automatic sustained use discount — kicks in after 25% monthly usage"},
        },
        "azure": {
            "reservation_1y": {"rate": 0.36, "condition": "1 year Azure reservation"},
            "reservation_3y": {"rate": 0.52, "condition": "3 year Azure reservation"},
            "hybrid_benefit": {"rate": 0.40, "condition": "Azure Hybrid Benefit — requires existing Windows Server or SQL Server licenses"},
        },
    }

    cloud_lower  = cloud.lower()
    discounts    = _DISCOUNT_RATES.get(cloud_lower, {})
    base_monthly = float((pricing_data or {}).get("minimum_monthly", 100) or 100)

    opportunities: list[dict] = []
    for mechanism, info in discounts.items():
        if "spot" in mechanism and usage_profile == "high":
            continue
        if mechanism == "sustained_auto" and usage_profile == "low":
            continue

        optimized = round(base_monthly * (1.0 - info["rate"]), 2)
        opportunities.append({
            "service":           service,
            "cloud":             cloud,
            "base_monthly":      round(base_monthly, 2),
            "optimized_monthly": optimized,
            "saving_pct":        round(info["rate"] * 100, 0),
            "mechanism":         mechanism,
            "condition":         info["condition"],
        })

    opportunities.sort(key=lambda x: x["saving_pct"], reverse=True)
    return opportunities


# ── Agent 04 Jinja2 template tool ─────────────────────────────────────────────

@tool
def render_jinja2_template(cloud_provider: str, resource_type: str,
                            variables_json: str) -> str:
    """Render a Jinja2 Terraform template with the provided variables.

    Reads the template from agents/templates/<cloud_provider>/<resource_type>.tf.j2
    and renders it with the given variables.

    Args:
        cloud_provider: One of 'aws', 'gcp', 'azure'.
        resource_type:  Template name without extension (e.g. 'compute', 'storage').
        variables_json: JSON string of variables dict to pass to the template.

    Returns:
        JSON string with: rendered (the .tf content), template_path, error (if any).
    """
    try:
        variables = json.loads(variables_json)
    except json.JSONDecodeError as e:
        return json.dumps({"rendered": None, "error": f"variables_json parse error: {e}"})

    template_path = os.path.join(TEMPLATES_DIR, cloud_provider, f"{resource_type}.tf.j2")

    if not os.path.exists(template_path):
        return json.dumps({
            "rendered": None,
            "template_path": template_path,
            "error": f"Template not found: {template_path}",
        })

    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
        env = Environment(
            loader=FileSystemLoader(os.path.dirname(template_path)),
            undefined=StrictUndefined,
        )
        template = env.get_template(os.path.basename(template_path))
        rendered = template.render(**variables)
        return json.dumps({"rendered": rendered, "template_path": template_path, "error": None})
    except Exception as e:
        return json.dumps({"rendered": None, "template_path": template_path, "error": str(e)})


# ── Re-exports from focused modules (backward compatibility) ──────────────────

from agents.iac_generator.hcl_merge import (       # noqa: E402
    _HCL_RES_HEADER,
    _SINGLETON_TF_FILES,
    _extract_blocks,
    _merge_hcl,
    write_terraform_file,
)

from agents.iac_generator.react_tools import (      # noqa: E402
    validate_terraform_block,
    get_rag_context_for_resource,
    read_generated_files,
)

from agents.iac_generator.reporting_tools import (  # noqa: E402
    gen_deploy_report,
    gen_rollback_report,
)

from agents.iac_generator.cloud_integration import (  # noqa: E402
    _fetch_vault_credentials,
    validate_target_credentials,
    check_deployment_readiness,
    get_deployment_context,
    render_deploy_template,
    execute_terraform_apply,
)

from agents.iac_generator.cicd_tools import (       # noqa: E402
    generate_cicd_pipeline,
)

__all__ = [
    # shared utilities
    "_get_output_dir", "OUTPUT_DIR", "TEMPLATES_DIR",
    "_normalize_provider_name", "_safe_dict", "_load_yaml",
    "_compute_weights", "_EMBEDDING_MODELS_DB",
    # scoring (kept here)
    "score_candidates", "compute_savings_opportunities",
    # template (kept here)
    "render_jinja2_template",
    # hcl_merge
    "_HCL_RES_HEADER", "_SINGLETON_TF_FILES", "_extract_blocks", "_merge_hcl",
    "write_terraform_file",
    # react_tools
    "validate_terraform_block", "get_rag_context_for_resource", "read_generated_files",
    # reporting_tools
    "gen_deploy_report", "gen_rollback_report",
    # cloud_integration
    "_fetch_vault_credentials", "validate_target_credentials",
    "check_deployment_readiness", "get_deployment_context",
    "render_deploy_template", "execute_terraform_apply",
    # cicd_tools
    "generate_cicd_pipeline",
]
