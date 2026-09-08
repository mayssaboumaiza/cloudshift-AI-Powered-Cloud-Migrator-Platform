"""
reporting_tools.py — Agent 05 deployment/rollback Markdown report generators.

Two LangChain tools:
  gen_deploy_report   — Markdown deployment summary (resources, URLs, errors).
  gen_rollback_report — Markdown rollback summary (destroyed, orphaned, costs).

Self-contained: no project imports, no network calls.
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

logger = logging.getLogger("Tools")


@tool
def gen_deploy_report(status: str, resources: str, errors: str, urls: str) -> str:
    """Generate a Markdown deployment report.

    Args:
        status:    Deployment status string — 'SUCCESS', 'PARTIAL', or 'FAILED'.
        resources: JSON string list of deployed resource dicts
                   [{name, type, region, status}].
        errors:    JSON string list of error dicts [{resource, message, severity}].
        urls:      JSON string list of output URL dicts [{name, url}].

    Returns:
        Markdown string of the final deployment report.
    """
    try:
        resource_list = json.loads(resources) if resources else []
    except json.JSONDecodeError:
        resource_list = []

    try:
        error_list = json.loads(errors) if errors else []
    except json.JSONDecodeError:
        error_list = []

    try:
        url_list = json.loads(urls) if urls else []
    except json.JSONDecodeError:
        url_list = []

    status_emoji = {"SUCCESS": "✅", "PARTIAL": "⚠️", "FAILED": "❌"}.get(status, "ℹ️")

    lines = [f"# Rapport de déploiement {status_emoji} — {status}\n"]

    if url_list:
        lines.append("## URLs de déploiement\n")
        for u in url_list:
            lines.append(f"- **{u.get('name', '')}**: {u.get('url', '')}")
        lines.append("")

    lines.append("## Ressources déployées\n")
    if resource_list:
        lines.append("| Ressource | Type | Région | Statut |")
        lines.append("|-----------|------|--------|--------|")
        for r in resource_list:
            lines.append(
                f"| {r.get('name','')} | {r.get('type','')} "
                f"| {r.get('region','')} | {r.get('status','')} |"
            )
    else:
        lines.append("_Aucune ressource déployée._")
    lines.append("")

    if error_list:
        lines.append("## Erreurs rencontrées\n")
        for e in error_list:
            sev = e.get("severity", "ERROR")
            lines.append(f"- **[{sev}]** `{e.get('resource','')}`: {e.get('message','')}")
        lines.append("")

    lines.append("---\n_Rapport généré par Cloud Migrator Agent 05_\n")
    return "\n".join(lines)


@tool
def gen_rollback_report(destroyed: str, orphaned: str, costs: str) -> str:
    """Generate a Markdown rollback report.

    Args:
        destroyed: JSON string list of destroyed resource names.
        orphaned:  JSON string list of orphaned resource dicts [{name, reason}].
        costs:     JSON string dict of cost info
                   {currency, monthly_savings, one_time_cost}.

    Returns:
        Markdown string of the rollback report.
    """
    try:
        destroyed_list = json.loads(destroyed) if destroyed else []
    except json.JSONDecodeError:
        destroyed_list = []

    try:
        orphaned_list = json.loads(orphaned) if orphaned else []
    except json.JSONDecodeError:
        orphaned_list = []

    try:
        cost_info = json.loads(costs) if costs else {}
    except json.JSONDecodeError:
        cost_info = {}

    lines = ["# Rapport de rollback ⏪\n"]

    lines.append("## Ressources détruites\n")
    if destroyed_list:
        for name in destroyed_list:
            lines.append(f"- `{name}`")
    else:
        lines.append("_Aucune ressource détruite._")
    lines.append("")

    if orphaned_list:
        lines.append("## Ressources orphelines (action manuelle requise)\n")
        lines.append("| Ressource | Raison |")
        lines.append("|-----------|--------|")
        for o in orphaned_list:
            lines.append(f"| {o.get('name','')} | {o.get('reason','')} |")
        lines.append("")

    if cost_info:
        currency = cost_info.get("currency", "USD")
        savings  = cost_info.get("monthly_savings", 0)
        one_time = cost_info.get("one_time_cost", 0)
        lines.append("## Coûts\n")
        lines.append(f"- Économies mensuelles récupérées : **{savings} {currency}/mois**")
        lines.append(f"- Coût unique du rollback : **{one_time} {currency}**")
        lines.append("")

    lines.append("---\n_Rapport généré par Cloud Migrator Agent 05_\n")
    return "\n".join(lines)
