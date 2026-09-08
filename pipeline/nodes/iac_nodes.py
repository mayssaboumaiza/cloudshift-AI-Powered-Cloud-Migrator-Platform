"""
iac_nodes.py - LangGraph nodes for the IaC generation and validation phase.

Responsibilities:
  validate_intent_node — deterministic business-constraint checks (region, budget, hardcoded secrets)
  validate_iac_node    — technical validation via the MCP IaC service (tf validate + Checkov + Infracost)

Note: generate_iac and fix_targeted are wrapped directly on run_agent_02 / run_agent_02_fix
in pipeline_graph.py (agent functions — not inline nodes).
"""
import logging
import os
import re
from pathlib import Path
from typing import Any

from agents.pipeline_state import MigrationState
from agents.node_decorator import publish_events
from agents.iac_generator import Agent02IaCValidator
from core.constants import INTENT_BUDGET_TOLERANCE, MAX_INTENT_REGEN

logger = logging.getLogger("Graph.IaC")

# ── Validator singleton (deterministic — calls IaCMCP) ───────────────────────
_validator = Agent02IaCValidator()

# ── IaC output directory ──────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _get_output_dir() -> Path:
    """Re-read MIGRATION_OUTPUT_DIR on every call — generator.py overrides it per migration."""
    _out_env = os.environ.get("MIGRATION_OUTPUT_DIR", "")
    if _out_env and os.path.isabs(_out_env):
        return Path(_out_env)
    return _PROJECT_ROOT / (_out_env or os.path.join("output", "migrated_app"))


# ── Regex for detecting hardcoded secrets in .tf files (fallback) ────────────
_SECRET_PATTERNS = re.compile(
    r'(password|secret|api_key|access_key|private_key|token)\s*=\s*"[^"]{6,}"',
    re.IGNORECASE,
)

# ── HCL2 structural parser ────────────────────────────────────────────────────
try:
    import hcl2
    _HAS_HCL2 = True
except ImportError:
    _HAS_HCL2 = False
    logger.warning("[IaC] python-hcl2 not installed — falling back to regex parsing for intent validation")


def _unquote(label: str) -> str:
    """Strip the surrounding quotes python-hcl2 v8+ keeps around block labels.

    hcl2 < v4 returned bare labels ('azurerm_storage_account'); v8 returns them
    quoted ('"azurerm_storage_account"'). Normalize to the bare form so downstream
    exact-match comparisons keep working across hcl2 versions.
    """
    if isinstance(label, str) and len(label) >= 2 and label[0] == '"' and label[-1] == '"':
        return label[1:-1]
    return label


def _clean_attrs(attrs: Any) -> dict[str, Any]:
    """Drop the '__is_block__' marker python-hcl2 v8+ injects into every block dict."""
    if not isinstance(attrs, dict):
        return {}
    return {k: v for k, v in attrs.items() if k != "__is_block__"}


def _parse_hcl_files(tf_contents: dict[str, str]) -> list[dict[str, Any]]:
    """Parse .tf file contents into a flat list of resource dicts via python-hcl2.

    Returns list of dicts with keys: resource_type, resource_name, attrs (dict).
    Falls back to empty list if hcl2 is unavailable or parse errors occur.
    """
    if not _HAS_HCL2:
        return []

    resources: list[dict[str, Any]] = []
    for fname, content in tf_contents.items():
        try:
            parsed = hcl2.loads(content)
            for block_type, block_list in parsed.items():
                if block_type != "resource":
                    continue
                for resource_block in (block_list if isinstance(block_list, list) else [block_list]):
                    for res_type, instances in resource_block.items():
                        for res_name, attrs in instances.items():
                            resources.append({
                                "resource_type": _unquote(res_type),
                                "resource_name": _unquote(res_name),
                                "attrs": _clean_attrs(attrs),
                                "_file": fname,
                            })
        except Exception as exc:
            logger.debug("[IaC] hcl2 parse error in %s: %s — will use regex fallback", fname, exc)
    return resources


def _get_attr(resources: list[dict], resource_type_substr: str, attr_key: str) -> list[Any]:
    """Collect attribute values from all resources whose type contains resource_type_substr."""
    values = []
    for r in resources:
        if resource_type_substr in r["resource_type"]:
            val = r["attrs"].get(attr_key)
            if val is not None:
                values.append(val)
    return values


# ─────────────────────────────────────────────────────────────────────────────
# validate_intent_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="validate_intent")
def validate_intent_node(state: MigrationState) -> MigrationState:
    """Deterministic business-constraint validation — zero LLM.

    Checks:
    1. Region: the user-declared region is present in the generated .tf files.
    2. Budget: estimated cost <= monthly_budget_usd × INTENT_BUDGET_TOLERANCE.
    3. Security: no hardcoded secrets in generated .tf files.
    """
    if state.get("needs_human_escalation"):
        logger.info("[IaC] validate_intent: skipping — needs_human_escalation already set.")
        return state

    logger.info("[IaC] validate_intent: running deterministic checks...")

    warnings = list(state.get("warnings") or [])
    intent_issues: list[str] = []

    arch = state.get("architecture_specs") or {}
    region = (arch.get("region", "") or state.get("target_region", "") or "").strip()
    budget = state.get("monthly_budget_usd")

    # Read generated .tf files
    tf_contents: dict[str, str] = {}
    output_path = _get_output_dir()
    if output_path.is_dir():
        for tf_file in output_path.glob("*.tf"):
            try:
                tf_contents[tf_file.name] = tf_file.read_text(encoding="utf-8")
            except Exception:
                pass

    all_tf = "\n".join(tf_contents.values())

    # Parse structured HCL2 representation for precise attribute extraction.
    # Falls back to regex if python-hcl2 is unavailable or a file fails to parse.
    hcl_resources = _parse_hcl_files(tf_contents)
    _use_hcl2 = bool(hcl_resources)
    if _use_hcl2:
        logger.debug("[IaC] validate_intent: using HCL2 parser (%d resource blocks)", len(hcl_resources))
    else:
        logger.debug("[IaC] validate_intent: HCL2 unavailable or no resources parsed — using regex fallback")

    # ── Check 1: region consistency ──────────────────────────────────────────
    if region and tf_contents:
        region_found = False
        if _use_hcl2:
            # Check all location/region attribute values across every resource
            for r in hcl_resources:
                for attr_key in ("location", "region", "zone"):
                    val = r["attrs"].get(attr_key, "")
                    if isinstance(val, str) and region.lower() in val.lower():
                        region_found = True
                        break
                if region_found:
                    break
            # Also fall back to raw text check (covers provider-level configs)
            if not region_found and region.lower() in all_tf.lower():
                region_found = True
        else:
            region_found = region.lower() in all_tf.lower()

        if not region_found:
            intent_issues.append(
                f"Region mismatch: user requested '{region}' but it was not found "
                "in any generated .tf file."
            )
        else:
            logger.info("[IaC] validate_intent: ✓ region '%s' found in .tf files", region)

    # ── Check 2: budget vs estimated cost ────────────────────────────────────
    migration_plan = state.get("migration_plan") or {}
    summary = migration_plan.get("summary", {})
    estimated_cost = float(summary.get("estimated_total_monthly", 0.0) or 0.0)

    if budget and estimated_cost > 0:
        if estimated_cost > float(budget) * INTENT_BUDGET_TOLERANCE:
            intent_issues.append(
                f"Budget exceeded: estimated monthly cost (${estimated_cost:.0f}) exceeds "
                f"declared budget (${budget:.0f}) by more than "
                f"{int((INTENT_BUDGET_TOLERANCE - 1) * 100)}%. "
                "Choose lower-tier SKUs (e.g. GP_Standard_D2s_v3 instead of D4/D8)."
            )
        else:
            logger.info(
                "[IaC] validate_intent: ✓ cost $%.0f within budget $%.0f",
                estimated_cost, budget,
            )

    # ── Check 3: hardcoded secrets ───────────────────────────────────────────
    _SECRET_ATTR_KEYS = {"password", "secret", "api_key", "access_key", "private_key", "token"}
    if _use_hcl2:
        for r in hcl_resources:
            for key, val in r["attrs"].items():
                if key.lower() in _SECRET_ATTR_KEYS and isinstance(val, str) and len(val) >= 6:
                    # Likely a real hardcoded value (not a var reference or empty)
                    if not val.startswith("${") and not val.startswith("var."):
                        intent_issues.append(
                            f"Hardcoded secret detected in {r['_file']} "
                            f"({r['resource_type']}.{r['resource_name']}): attribute '{key}'. "
                            "Use variables or a secrets manager instead."
                        )
    else:
        for fname, content in tf_contents.items():
            matches = _SECRET_PATTERNS.findall(content)
            if matches:
                keys = ", ".join(m[0] for m in matches[:3])
                intent_issues.append(
                    f"Hardcoded secret detected in {fname}: {keys}. "
                    "Use variables or a secrets manager instead."
                )

    # ── Check 4: production workload — backup retention must be set ──────────
    # Paper (Knowledge Injection 2025): "Incompleteness" errors (26.5%) — required
    # arguments missing for business context (not just syntax). A production DB
    # with backup_retention_days = 0 is a semantic intent failure even if the
    # HCL is syntactically valid.
    is_production = bool(state.get("is_production", True))
    if is_production and all_tf:
        if _use_hcl2:
            backup_vals = _get_attr(hcl_resources, "", "backup_retention_days")
            for val in backup_vals:
                try:
                    if int(val) <= 1:
                        intent_issues.append(
                            "Production intent violation: backup_retention_days is 0 or 1. "
                            "Set backup_retention_days >= 7 for production workloads."
                        )
                        break
                except (TypeError, ValueError):
                    pass
            # geo_redundant_backup check for PostgreSQL / MySQL flexible server
            for r in hcl_resources:
                if r["resource_type"] in ("azurerm_postgresql_flexible_server", "azurerm_mysql_flexible_server"):
                    geo = r["attrs"].get("geo_redundant_backup_enabled")
                    if geo is False or geo == "false":
                        logger.info(
                            "[IaC] validate_intent: geo_redundant_backup=false on production — warning only"
                        )
                        warnings.append(
                            "[Intent] geo_redundant_backup_enabled=false on a production-grade "
                            "database. Consider enabling for HA requirements."
                        )
        else:
            if re.search(r'backup_retention_days\s*=\s*[0-1](?!\d)', all_tf):
                intent_issues.append(
                    "Production intent violation: backup_retention_days is 0 or 1. "
                    "Set backup_retention_days >= 7 for production workloads."
                )
            if re.search(r'azurerm_postgresql_flexible_server|azurerm_mysql_flexible_server', all_tf):
                if re.search(r'geo_redundant_backup_enabled\s*=\s*false', all_tf):
                    warnings.append(
                        "[Intent] geo_redundant_backup_enabled=false on a production-grade "
                        "database. Consider enabling for HA requirements."
                    )

    # ── Check 5: TLS minimum version ─────────────────────────────────────────
    if _use_hcl2:
        tls_vals = _get_attr(hcl_resources, "", "min_tls_version")
        for val in tls_vals:
            if isinstance(val, str) and val.upper() in ("TLS1_0", "TLS1_1", "TLS1.0", "TLS1.1"):
                intent_issues.append(
                    "Security intent violation: min_tls_version is set to TLS 1.0 or 1.1. "
                    'Set min_tls_version = "TLS1_2" on all storage accounts and function apps.'
                )
                break
    elif all_tf and re.search(r'min_tls_version\s*=\s*"TLS1[._][01]"', all_tf, re.IGNORECASE):
        intent_issues.append(
            "Security intent violation: min_tls_version is set to TLS 1.0 or 1.1. "
            'Set min_tls_version = "TLS1_2" on all storage accounts and function apps.'
        )

    if intent_issues:
        logger.warning("[IaC] validate_intent: %d issue(s) found", len(intent_issues))
        warnings.extend([f"[Intent] {issue}" for issue in intent_issues])
    else:
        logger.info("[IaC] validate_intent: ✓ all checks passed")

    artifacts = dict(state.get("artifacts") or {})
    artifacts["intent_issues"] = intent_issues
    artifacts["intent_validation_passed"] = len(intent_issues) == 0

    return {
        "warnings": warnings,
        "artifacts": artifacts,
        "intent_issues": intent_issues,
    }


# ─────────────────────────────────────────────────────────────────────────────
# validate_iac_node
# ─────────────────────────────────────────────────────────────────────────────

@publish_events(phase="validate_iac")
def validate_iac_node(state: MigrationState) -> MigrationState:
    """Technical IaC validation via the MCP IaC service (tf validate + Checkov + Infracost).

    MCP is the sole validation authority. iac_validation_success is the exit gate.
    MCP unavailability is a pipeline failure — no local fallback.
    """
    logger.info("[IaC] Running IaC technical validation...")
    return _validator.validate_pipeline(state)
