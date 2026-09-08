"""
multirepo_merger.py - Multi-repo dependency graph fusion.

Merges N `dependency_graph` dicts (one per repo) into a single graph that
reflects the full platform. Inter-repo dependencies are inferred from shared
external handles (S3 bucket names, URLs, queue names, env var names).

Pipeline:
  1. canonical_id — stable hash so the same logical service appears once
     even if detected in multiple repos.
  2. Node fusion — dedupe by canonical_id, take max(score), union provenance.
  3. Edge inference — scan external_handles to link producer→consumer across repos.

Pitfalls addressed:
  - Keeps `repo_origin` on each node so Agent 03 still knows where to write.
  - Detects cross-repo cycles (logged as WARNINGs; not auto-resolved).
  - Flags conflicts when the same canonical service has divergent config
    (e.g., different regions for the same bucket name).

Usage:
    merged = merge_graphs([graph_a, graph_b, graph_c])
    # merged["resources"]["nodes"], merged["resources"]["edges"]
    # merged["cross_repo_edges"], merged["conflicts"]
"""
from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from typing import Any, Iterable

logger = logging.getLogger("MultiRepoMerger")


def canonical_id(provider: str, service_type: str, logical_name: str) -> str:
    """Stable hash across repos — same key = same canonical service."""
    key = f"{(provider or '').lower()}:{(service_type or '').lower()}:{(logical_name or '').lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _norm_handles(node: dict) -> list[str]:
    """Extract external handles used to link this node across repos.

    Sources (best to weakest):
      - explicit `external_handles` (bucket names, ARNs, URLs, queue names)
      - `outputs` (Terraform outputs — this node produces the handle)
      - `env_consumed` (os.getenv keys — this node consumes the handle)
    """
    handles: set[str] = set()
    for h in node.get("external_handles") or []:
        if h:
            handles.add(str(h).strip().lower())
    for o in node.get("outputs") or []:
        if isinstance(o, dict) and o.get("value"):
            handles.add(str(o["value"]).strip().lower())
        elif isinstance(o, str):
            handles.add(o.strip().lower())
    for e in node.get("env_consumed") or []:
        if e:
            handles.add(str(e).strip().lower())
    return [h for h in handles if h]


def _fuse_node(existing: dict, incoming: dict) -> dict:
    """Merge two nodes with the same canonical_id.

    - score: max (a shared service is at least as critical as the higher estimate)
    - repo_origin: collected into `repos` list
    - provenance: unioned
    - external_handles: unioned
    - region/config conflicts: captured in `_conflicts`
    """
    merged = dict(existing)
    merged["score"] = max(float(existing.get("score", 0)), float(incoming.get("score", 0)))

    repos = set(merged.get("repos") or [])
    for r in (existing.get("repo_origin"), incoming.get("repo_origin")):
        if r:
            repos.add(r)
    merged["repos"] = sorted(repos)

    prov = set(merged.get("provenance") or [])
    for p in (existing.get("provenance"), incoming.get("provenance")):
        if isinstance(p, str) and p:
            prov.add(p)
        elif isinstance(p, list):
            prov.update(p)
    merged["provenance"] = sorted(prov)

    merged["external_handles"] = sorted(set(_norm_handles(existing)) | set(_norm_handles(incoming)))

    # Flag config divergences (region, storage_class, etc.)
    conflicts = list(existing.get("_conflicts") or [])
    for key in ("region", "storage_class", "encryption", "tier"):
        a, b = existing.get(key), incoming.get(key)
        if a and b and a != b:
            conflicts.append({
                "field": key,
                "values": {existing.get("repo_origin", "repo_a"): a, incoming.get("repo_origin", "repo_b"): b},
            })
    if conflicts:
        merged["_conflicts"] = conflicts

    return merged


def merge_graphs(graphs: Iterable[dict]) -> dict:
    """Fuse multiple per-repo dependency_graph dicts into one.

    Each input graph is expected to have the shape produced by stack_analyzer:
      {
        "detected_cloud": str,
        "resources": {"nodes": [...], "edges": [...]},
        "repo_origin": "owner/name",          # tagged by caller
      }

    Returns a graph with:
      - resources.nodes         — deduped by canonical_id
      - resources.edges         — original intra-repo edges + inferred cross-repo
      - cross_repo_edges        — only the inferred ones (for UI highlighting)
      - conflicts               — list of node-level config conflicts
      - cycles                  — list of detected cross-repo cycles (not resolved)
    """
    graphs = list(graphs)
    if not graphs:
        return {"resources": {"nodes": [], "edges": []}, "cross_repo_edges": [],
                "conflicts": [], "cycles": []}

    # ── Step 1: canonicalise + fuse nodes ────────────────────────────────
    by_canonical: dict[str, dict] = {}
    node_origin_map: dict[str, str] = {}  # original_node_id → canonical_id

    for g in graphs:
        repo = g.get("repo_origin") or ""
        resources = g.get("resources") or {}
        for raw in resources.get("nodes", []) or []:
            node = dict(raw)
            node.setdefault("repo_origin", repo)
            cid = node.get("canonical_id") or canonical_id(
                node.get("cloud_provider", "") or node.get("provider", ""),
                node.get("service_type", "") or node.get("type", ""),
                node.get("name", "") or node.get("id", ""),
            )
            node["canonical_id"] = cid
            node_origin_map[f"{repo}::{node.get('id', '')}"] = cid

            # Always normalise external_handles so single-instance nodes
            # also participate in cross-repo inference.
            node["external_handles"] = _norm_handles(node)

            if cid in by_canonical:
                by_canonical[cid] = _fuse_node(by_canonical[cid], node)
            else:
                node["repos"] = [repo] if repo else []
                by_canonical[cid] = node

    merged_nodes = list(by_canonical.values())
    conflicts = [{"canonical_id": n["canonical_id"], "conflicts": n.pop("_conflicts")}
                 for n in merged_nodes if n.get("_conflicts")]

    # ── Step 2: remap intra-repo edges to canonical ids ──────────────────
    merged_edges: list[dict] = []
    seen_edges: set[tuple] = set()
    for g in graphs:
        repo = g.get("repo_origin") or ""
        resources = g.get("resources") or {}
        for raw in resources.get("edges", []) or []:
            src = node_origin_map.get(f"{repo}::{raw.get('source', '')}") or raw.get("source")
            dst = node_origin_map.get(f"{repo}::{raw.get('target', '')}") or raw.get("target")
            if not src or not dst or src == dst:
                continue
            key = (src, dst, raw.get("relation_type"))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            merged_edges.append({
                "source": src,
                "target": dst,
                "relation_type": raw.get("relation_type"),
                "edge_type": raw.get("edge_type", "depends_on"),
                "weight": float(raw.get("weight", 1.0)),
                "evidence": raw.get("evidence"),
            })

    # ── Step 3: infer cross-repo edges from shared handles ───────────────
    cross_repo_edges = _infer_cross_repo_edges(by_canonical, merged_edges, seen_edges)
    merged_edges.extend(cross_repo_edges)

    # ── Step 4: detect cross-repo cycles (don't auto-resolve) ────────────
    cycles = _detect_cycles(by_canonical, merged_edges)
    if cycles:
        logger.warning("Detected %d cross-repo cycle(s): %s", len(cycles), cycles[:3])

    return {
        "detected_cloud": graphs[0].get("detected_cloud"),
        "resources": {"nodes": merged_nodes, "edges": merged_edges},
        "cross_repo_edges": cross_repo_edges,
        "conflicts": conflicts,
        "cycles": cycles,
        "repos": sorted({g.get("repo_origin", "") for g in graphs if g.get("repo_origin")}),
    }


def _infer_cross_repo_edges(
    by_canonical: dict[str, dict],
    existing_edges: list[dict],
    seen_edges: set[tuple],
) -> list[dict]:
    """Link producer→consumer across repos via shared external handles."""
    # Build handle → [canonical_ids] index
    handle_owners: dict[str, list[str]] = defaultdict(list)
    for cid, node in by_canonical.items():
        for h in node.get("external_handles") or []:
            handle_owners[h].append(cid)

    new_edges: list[dict] = []
    for handle, owners in handle_owners.items():
        # Multi-repo: the same handle referenced from >1 canonical node
        unique = list(dict.fromkeys(owners))  # preserve order, dedupe
        if len(unique) < 2:
            continue

        # Determine producer vs consumers.
        # Heuristic: if a node type is in the "storage/queue/topic" family it's
        # more likely the producer. Otherwise the first owner wins.
        producer = _pick_producer(unique, by_canonical)
        for consumer in unique:
            if consumer == producer:
                continue
            # Skip if the producers' repo == consumer's repo (intra-repo, already handled)
            p_repos = set(by_canonical[producer].get("repos") or [])
            c_repos = set(by_canonical[consumer].get("repos") or [])
            if p_repos and c_repos and p_repos == c_repos:
                continue
            key = (producer, consumer, "cross_repo_dependency")
            if key in seen_edges:
                continue
            seen_edges.add(key)
            new_edges.append({
                "source": producer,
                "target": consumer,
                "relation_type": "cross_repo_dependency",
                "edge_type": "cross_repo",
                "weight": 1.0,
                "evidence": {"shared_handle": handle, "repos": list(p_repos | c_repos)},
            })

    return new_edges


_PRODUCER_TYPES = {
    "storage", "object-storage", "bucket",
    "queue", "message-queue", "topic",
    "database", "managed-relational-db", "key-value-nosql",
    "stream", "event-streaming",
}


def _pick_producer(candidates: list[str], by_canonical: dict[str, dict]) -> str:
    """Best-effort: pick the node whose type looks like a producer."""
    for cid in candidates:
        t = str(by_canonical[cid].get("service_type") or by_canonical[cid].get("type") or "").lower()
        if t in _PRODUCER_TYPES:
            return cid
    return candidates[0]


def _detect_cycles(
    by_canonical: dict[str, dict],
    edges: list[dict],
) -> list[list[str]]:
    """Return cycles in the combined graph. Only reports cycles that touch
    at least two different repos (intra-repo cycles are a different problem)."""
    try:
        import networkx as nx  # lazy import; already a dep via stack_analyzer
    except ImportError:
        return []

    g = nx.DiGraph()
    for cid, n in by_canonical.items():
        g.add_node(cid, repos=set(n.get("repos") or []))
    for e in edges:
        g.add_edge(e["source"], e["target"])

    cycles: list[list[str]] = []
    try:
        for cyc in nx.simple_cycles(g):
            touched = set()
            for cid in cyc:
                touched |= set(by_canonical.get(cid, {}).get("repos") or [])
            if len(touched) >= 2:
                cycles.append(cyc)
    except Exception as exc:  # pragma: no cover
        logger.warning("Cycle detection failed: %s", exc)
    return cycles
