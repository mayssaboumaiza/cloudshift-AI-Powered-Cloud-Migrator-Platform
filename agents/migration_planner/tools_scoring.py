"""
tools_scoring.py — Agent 01 backward-compatibility facade.

ROLE   : Single import point for all Agent 01 tools, kept so that planner.py
         and agents/analyzer/core.py do not need to know which sub-module owns
         each symbol.
REASON : The original monolithic file was split into three focused modules.
         Removing this facade would require updating every caller simultaneously.
         Migrate callers gradually and delete this file once all direct imports
         are in place.
SEE    :
  code_analysis_tools.py    — Python AST/regex analysis (4 tools)
  service_lookup_tools.py   — Live cloud API lookups   (6 tools)
  scoring_decision_tools.py — SAW scoring + 7R decisions (3 tools)
"""

from services.stack_analyzer.code_analysis_tools import (
    ast_parse_file,
    compute_dependency_score,
    detect_cloud_provider,
    detect_ai_framework,
)

from agents.migration_planner.service_lookup_tools import (
    check_region_availability,
    check_budget_fit,
    check_service_maturity,
    estimate_cost,
    check_region,
    # shared data tables (used by stack_analyzer and tests)
    _KNOWN_REGIONS_STATIC,
    _PROVIDER_ALIASES,
    _PROVIDER_RESOURCE_PREFIXES,
    # TTL caches (accessed by tests to invalidate between runs)
    _MATURITY_CACHE,
    _REGION_CACHE,
    _PRICING_CACHE,
    # optional HTTP flag + module (patched by tests)
    _HAS_HTTPX,
)
import agents.migration_planner.service_lookup_tools as _slt
# expose httpx at module level so tests can do: patch.object(tools_mod.httpx, ...)
try:
    import httpx  # noqa: F401 — re-export for test patching
except ImportError:
    httpx = _slt.httpx if hasattr(_slt, "httpx") else None  # type: ignore[assignment]

from agents.migration_planner.scoring_decision_tools import (
    score_service_candidates,
    decide_7r_strategy,
    decide_7r_strategy_v2,
    lookup_terraform_mapping,
)

__all__ = [
    # code_analysis_tools
    "ast_parse_file",
    "compute_dependency_score",
    "detect_cloud_provider",
    "detect_ai_framework",
    # service_lookup_tools
    "check_region_availability",
    "check_budget_fit",
    "check_service_maturity",
    "estimate_cost",
    "check_region",
    # scoring_decision_tools
    "score_service_candidates",
    "decide_7r_strategy",
    "decide_7r_strategy_v2",
    "lookup_terraform_mapping",
    # caches + HTTP flag (re-exported for test access)
    "_MATURITY_CACHE",
    "_REGION_CACHE",
    "_PRICING_CACHE",
    "_HAS_HTTPX",
    "httpx",
]
