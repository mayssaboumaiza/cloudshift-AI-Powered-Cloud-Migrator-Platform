"""
plan_metrics.py — Agent 01 batching, complexity assessment, and timeline calculation.

Responsibilities:
  - Group dependency graph nodes into per-category batches for homogeneous LLM calls.
  - Assess overall migration complexity from the strategy breakdown.
  - Compute the estimated migration timeline from LLM-assessed effort_days (no YAML needed).
  - Parse free-form timeline strings into integer months.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("Agent01")


def _batch_by_category(nodes: list[dict]) -> dict[str, list[dict]]:
    """Group dependency graph nodes by category for homogeneous LLM invocations.

    Returns:
        Dict mapping category name → list of nodes in that category.
    """
    batches: dict[str, list[dict]] = {}
    for node in nodes:
        category = node.get("type") or node.get("category") or "other"
        batches.setdefault(category, []).append(node)
    return batches


def _assess_complexity(strategies: dict[str, int]) -> str:
    """Derive overall migration complexity from the strategy breakdown.

    Returns 'high', 'medium', or 'low' based on the fraction of REFACTOR/REARCHITECT services.
    """
    refactor = strategies.get("REFACTOR", 0) + strategies.get("REARCHITECT", 0)
    total    = sum(strategies.values()) or 1
    ratio    = refactor / total
    if ratio > 0.5:
        return "high"
    if ratio > 0.2:
        return "medium"
    return "low"


def _compute_dynamic_weeks(services: list[dict]) -> int:
    """Estimate the migration timeline in weeks from LLM-assessed effort_days.

    Formula: total_weeks = sum(effort_days for active services) / 5 / 2
      - effort_days : GPT-4o estimate per service (already in the plan)
      - / 5         : convert workdays to calendar weeks
      - / 2         : 2-team parallelisation factor
    No YAML or static reference needed — the LLM's own assessment drives the timeline.
    Clamped to [2, 52] weeks.
    """
    active_effort = sum(
        s.get("effort_days", 5) or 5
        for s in services
        if (s.get("strategy_7r") or s.get("strategy", "")).upper()
        not in ("RETIRE", "RETAIN")
    )
    weeks = max(2, min(52, int(active_effort / 5 / 2) + 1))
    logger.debug(f"Agent01: dynamic timeline — {active_effort} effort-days → {weeks} weeks")
    return weeks


def _compute_savings(services: list[dict]) -> tuple[float, float]:
    """Compute realistic savings from the migration plan.

    Per-strategy savings rates (source: AWS Migration Evaluator reports,
    Azure TCO Calculator, McKinsey cloud economics research 2023):
      RETIRE     100 % — service eliminated, full cost removed
      REPURCHASE  25 % — SaaS replaces custom infra; licensing offset by no-ops cost
      REARCHITECT 30 % — cloud-native redesign optimises resource sizing
      REFACTOR    25 % — re-architected integration, managed-service swap
      REPLATFORM  18 % — cloud-native optimisation (auto-scaling, spot, right-sizing)
      REHOST       8 % — lift-and-shift; minor savings from reserved instances
      RELOCATE     4 % — region change only; marginal networking savings
      RETAIN       0 % — service stays on-premise, no migration

    Args:
        services: list of service dicts from the migration plan, each having
                  'strategy_7r' (str) and 'monthly_cost_eur' (float).

    Returns:
        (savings_pct, optimized_monthly_eur)
          savings_pct          — weighted-average savings percentage (0–100)
          optimized_monthly_eur — projected monthly cost after migration
    """
    _SAVINGS_BY_STRATEGY: dict[str, float] = {
        "RETIRE":      100.0,
        "REPURCHASE":   25.0,
        "REARCHITECT":  30.0,
        "REFACTOR":     25.0,
        "REPLATFORM":   18.0,
        "REHOST":        8.0,
        "RELOCATE":      4.0,
        "RETAIN":        0.0,
    }

    total_cost     = 0.0
    optimized_cost = 0.0

    for svc in services:
        cost     = float(svc.get("monthly_cost_eur", 0) or 0)
        strategy = (svc.get("strategy_7r") or svc.get("strategy") or "REPLATFORM").upper()
        rate     = _SAVINGS_BY_STRATEGY.get(strategy, 18.0)
        total_cost     += cost
        optimized_cost += cost * (1.0 - rate / 100.0)

    if total_cost == 0:
        return 0.0, 0.0

    savings_pct = round((1.0 - optimized_cost / total_cost) * 100, 1)
    return savings_pct, round(optimized_cost, 2)


def _parse_timeline_months(timeline_str: str) -> int:
    """Convert free-form timeline string to an integer number of months.

    Handles: "3_months", "6 months", "ASAP", bare integers.
    Defaults to 3 if unparseable.
    """
    if not timeline_str:
        return 3
    s = str(timeline_str).lower().replace("_", " ").replace("-", " ")
    if "asap" in s:
        return 1
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 3
