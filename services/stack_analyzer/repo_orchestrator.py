"""
sa_repo_orchestrator.py — Multi-repo graph orchestration utilities.

Functions:
  _detect_inter_repo_edges  — Find cross-repo service call edges via env-var patterns.
  _merge_dependency_graphs  — Merge N per-repo graphs into a virtual monorepo.
  _classify_infra_file      — Classify a file path as a known IaC type key.

Note: _analyze_single_repo is intentionally kept in stack_analyzer.py because
it calls _cached_fetch, _parse_iac_files, and _finalize_iac_graph — orchestration
functions that would create a circular import if moved here.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from services.stack_analyzer.graph_builder import _empty_graph

logger = logging.getLogger("StackAnalyzer")


# ── Patterns for inter-repo URL / hostname env-var detection ─────────────────

_INTER_REPO_URL_PATTERNS: list[re.Pattern] = [
    re.compile(
        r'(?:SERVICE|API|URL|HOST|ENDPOINT|URI|ADDR)\s*=\s*["\']?([a-zA-Z][a-zA-Z0-9_-]+)["\']?',
        re.IGNORECASE,
    ),
    re.compile(
        r'os\.getenv\(["\']([A-Z][A-Z0-9_]+(?:_URL|_HOST|_ENDPOINT|_SERVICE|_API))["\']',
        re.IGNORECASE,
    ),
]


def _detect_inter_repo_edges(
    all_graphs: list[dict],
    all_source_files: dict[str, dict[str, str]],
) -> list[dict]:
    """Detect edges between services from different repos via env-var patterns.

    Looks for env vars whose NAME contains the service ID of another repo's service.
    Example: if repo-A has PAYMENT_SERVICE_URL and repo-B exposes service "payment",
             we infer edge repo-A:main → repo-B:payment with relation "calls".

    Args:
        all_graphs:       List of per-repo dependency_graph dicts.  Each must carry
                          a ``_repo_label`` key set by the caller.
        all_source_files: Dict mapping repo_label → {filepath: source_text}.

    Returns:
        List of edge dicts: {from, to, relation, weight, inter_repo: True}
    """
    # Build lookup: service_id → repo_label
    svc_to_repo: dict[str, str] = {}
    for g in all_graphs:
        repo_label = g.get("_repo_label", "repo")
        for node in g.get("resources", {}).get("nodes", []):
            svc_to_repo[node["id"].lower()] = repo_label

    edges: list[dict] = []
    seen_edges: set[tuple[str, str]] = set()

    for repo_label, src_files in all_source_files.items():
        for fname, content in src_files.items():
            for svc_id, target_repo in svc_to_repo.items():
                if target_repo == repo_label:
                    continue  # intra-repo refs — handled by _extract_tf_cross_refs
                if re.search(rf'\b{re.escape(svc_id)}\b', content, re.IGNORECASE):
                    edge_key = (repo_label, target_repo + ":" + svc_id)
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            "from":       repo_label,
                            "to":         f"{target_repo}:{svc_id}",
                            "relation":   "calls",
                            "weight":     1,
                            "inter_repo": True,
                        })
    return edges


def _merge_dependency_graphs(
    graphs: list[dict],
    inter_repo_edges: list[dict] | None = None,
) -> dict:
    """Merge N per-repo dependency graphs into a single virtual monorepo graph.

    Each node id is prefixed with the repo label to avoid collisions:
      "s3" from repo "backend" → "backend:s3"

    The dominant cloud is the one seen most across all repos.  AI stack entries
    are merged by taking the first non-empty value for each key.

    Args:
        graphs:            Per-repo dependency_graph dicts.
        inter_repo_edges:  Optional edges from _detect_inter_repo_edges to inject.

    Returns:
        Single merged dependency_graph dict with repo_count and repos metadata.
    """
    if not graphs:
        return _empty_graph("No repos produced a valid dependency graph")

    if len(graphs) == 1:
        return graphs[0]

    merged_nodes: list[dict] = []
    merged_edges: list[dict] = []
    cloud_votes: dict[str, int] = {}
    files_analyzed = 0

    for g in graphs:
        repo_label   = g.get("_repo_label", "repo")
        cloud        = g.get("detected_cloud", "unknown")
        if cloud != "unknown":
            cloud_votes[cloud] = cloud_votes.get(cloud, 0) + 1
        files_analyzed += g.get("files_analyzed", 0)

        for node in g.get("resources", {}).get("nodes", []):
            prefixed = dict(node)
            prefixed["id"]   = f"{repo_label}:{node['id']}"
            prefixed["repo"] = repo_label
            merged_nodes.append(prefixed)

        for edge in g.get("resources", {}).get("edges", []):
            merged_edges.append({
                **edge,
                "from": f"{repo_label}:{edge['from']}",
                "to":   f"{repo_label}:{edge['to']}",
            })

    for edge in (inter_repo_edges or []):
        merged_edges.append(edge)

    dominant_cloud = (
        max(cloud_votes, key=lambda k: cloud_votes[k]) if cloud_votes else "unknown"
    )

    # Merge ai_stack: first non-empty value wins across repos
    merged_ai: dict[str, Any] = {}
    for g in graphs:
        for k, v in (g.get("ai_stack") or {}).items():
            if v and k not in merged_ai:
                merged_ai[k] = v

    merged = _empty_graph()
    merged["detected_cloud"]         = dominant_cloud
    merged["cloud_detection_method"] = "multi_repo_majority"
    merged["resources"]              = {"nodes": merged_nodes, "edges": merged_edges}
    merged["files_analyzed"]         = files_analyzed
    merged["ai_stack"]               = merged_ai
    merged["repo_count"]             = len(graphs)
    merged["repos"]                  = [
        g.get("_repo_label", f"repo_{i}") for i, g in enumerate(graphs)
    ]
    return merged


def _classify_infra_file(fpath: str) -> str | None:
    """Return the IaC type key for a file path, or None if not recognised as IaC.

    Extends the standard github_tools classifier with ARM JSON and Helm detection.

    Returns:
        One of "terraform", "bicep", "cloudformation", "helm", "arm", or None.
    """
    fname = fpath.lower()
    if fname.endswith(".tf"):
        return "terraform"
    if fname.endswith(".bicep"):
        return "bicep"
    if fname.endswith((".yaml", ".yml")):
        basename = fpath.split("/")[-1].lower()
        if basename in ("chart.yaml", "values.yaml", "values-prod.yaml", "values-dev.yaml"):
            return "helm"
        return "cloudformation"
    if fname.endswith(".json") and any(
        kw in fname for kw in ("azuredeploy", "arm", "template", "maintemplate")
    ):
        return "arm"
    return None
