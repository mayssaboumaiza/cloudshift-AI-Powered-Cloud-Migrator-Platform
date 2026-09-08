"""
sa_graph_builder.py — Dependency graph construction and analysis utilities.

Functions:
  _infer_relation          — Infer edge semantic from API method name.
  _build_dependency_edges  — Build oriented edges from API call list.
  _build_nx_graph          — Construct a NetworkX DiGraph (optional).
  _detect_cycles           — List simple cycles in a DiGraph.
  _build_migration_priority — Topological/heuristic migration order.
  _build_services_inventory — Flat list of all graph nodes.
  _empty_graph             — Return an empty dependency_graph template.
  _is_infra_sdk            — Check if a service is an infrastructure SDK.
  _apply_dampening         — Suppress score for excluded infra SDKs.
"""
from __future__ import annotations
import logging
from typing import Any
try:
    import networkx as nx
except ImportError:
    nx = None  # type: ignore[assignment]

from services.stack_analyzer.constants import _RELATION_PATTERNS, _TYPE_PRIORITY, _INFRA_SDKS

logger = logging.getLogger("StackAnalyzer")

def _infer_relation(method_name: str) -> str:
    method_lower = method_name.lower()
    for relation, patterns in _RELATION_PATTERNS.items():
        if any(p in method_lower for p in patterns):
            return relation
    return "uses"


def _build_dependency_edges(all_api_calls: list, resource_set: set) -> list:
    """Build oriented dependency edges. For IaC-only analysis, api_calls is always []."""
    edge_counts: dict[tuple[str, str], dict] = {}
    for call in all_api_calls:
        caller_svc = str(call.get("caller_service", call.get("object", ""))).lower()
        target_svc = str(call.get("first_arg", "")).lower()
        method = str(call.get("method", "")).lower()
        if caller_svc and target_svc and caller_svc != target_svc:
            if caller_svc in resource_set and target_svc in resource_set:
                edge_key = (caller_svc, target_svc)
                if edge_key not in edge_counts:
                    edge_counts[edge_key] = {
                        "from": caller_svc, "to": target_svc,
                        "relation": _infer_relation(method), "weight": 0,
                    }
                edge_counts[edge_key]["weight"] += 1
    return list(edge_counts.values())


def _build_nx_graph(nodes: list[dict], edges: list[dict]) -> Any:
    if nx is None:
        return None
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"], **{k: v for k, v in n.items() if k != "id"})
    for e in edges:
        G.add_edge(e["from"], e["to"],
                   relation=e.get("relation", "uses"),
                   weight=e.get("weight", 1))
    return G


def _detect_cycles(G: Any) -> list[list[str]]:
    if G is None or nx is None:
        return []
    try:
        return [list(c) for c in nx.simple_cycles(G)]
    except Exception:
        return []


def _build_migration_priority(dependency_graph: dict) -> list:
    """Produit l'ordre de migration via topological sort ou tri heuristique."""
    resources_data = dependency_graph.get("resources", {})
    nodes = resources_data.get("nodes", []) if isinstance(resources_data, dict) else []
    edges = resources_data.get("edges", []) if isinstance(resources_data, dict) else []
    node_map = {n["id"]: n for n in nodes}

    topo_order: list[str] | None = None
    if nx is not None and nodes and edges:
        G = _build_nx_graph(nodes, edges)
        if G is not None and nx.is_directed_acyclic_graph(G):
            try:
                topo_order = list(nx.topological_sort(G))
            except Exception:
                topo_order = None

    if topo_order is not None:
        result = []
        for i, svc_id in enumerate(topo_order):
            n = node_map.get(svc_id, {"id": svc_id})
            svc_type = n.get("type", "unknown")
            result.append({
                "service": svc_id,
                "score": n.get("score", 1),
                "complexity": n.get("complexity", "LOW"),
                "migrate_order": i + 1,
                "type": svc_type,
                "type_priority": _TYPE_PRIORITY.get(svc_type, 8),
            })
        return result

    decorated = []
    for n in nodes:
        svc_type = n.get("type", "unknown")
        type_prio = _TYPE_PRIORITY.get(svc_type, 8)
        decorated.append((n, type_prio))
    decorated.sort(key=lambda x: (x[1], -x[0].get("score", 0), x[0]["id"]))
    return [
        {
            "service": n["id"],
            "score": n.get("score", 1),
            "complexity": n.get("complexity", "LOW"),
            "migrate_order": i + 1,
            "type": n.get("type", "unknown"),
            "type_priority": tp,
        }
        for i, (n, tp) in enumerate(decorated)
    ]


def _build_services_inventory(dependency_graph: dict) -> list:
    resources_data = dependency_graph.get("resources", {})
    nodes = resources_data.get("nodes", []) if isinstance(resources_data, dict) else []
    return [
        {
            "service": n["id"],
            "type": n.get("type", "unknown"),
            "score": n.get("score", 1),
            "complexity": n.get("complexity", "LOW"),
            "cloud": n.get("cloud", "unknown"),
            "source": n.get("source", "iac"),
        }
        for n in nodes
    ]


def _empty_graph(error: str = "") -> dict:
    base = {
        "detected_cloud": "unknown",
        "resources": {"nodes": [], "edges": []},
        "unused_resources": [],
        "framework": None,
        "llm_provider": None,
        "vector_db": None,
        "embedding_model": None,
        "files_analyzed": 0,
    }
    if error:
        base["error"] = error
    return base


def _is_infra_sdk(service_name: str, source_cloud: str) -> bool:
    """Return True if service_name is an infrastructure SDK that should be excluded."""
    excluded = _INFRA_SDKS.get(source_cloud, [])
    return service_name in excluded


def _apply_dampening(service_name: str, raw_score: int, source_cloud: str) -> int:
    if _is_infra_sdk(service_name, source_cloud):
        return 0  # Excluded infra SDK — score suppressed
    return raw_score
