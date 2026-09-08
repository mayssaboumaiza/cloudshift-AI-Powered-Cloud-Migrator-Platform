/**
 * Transforms API data shapes into React Flow nodes and edges.
 * No layout — positions are set to { x: 0, y: 0 }; layout.js fills them in.
 */

export const TYPE_COLORS = {
  compute:    "#3b82f6",
  storage:    "#f59e0b",
  database:   "#8b5cf6",
  network:    "#06b6d4",
  iam:        "#ef4444",
  messaging:  "#10b981",
  monitoring: "#f97316",
  ai:         "#a855f7",
  other:      "#94a3b8",
};

export const CLOUD_COLORS = {
  aws:   "#FF9900",
  gcp:   "#4285F4",
  azure: "#0078D4",
};

export const STRATEGY_COLORS = {
  REHOST:     "#22c55e",
  REPLATFORM: "#eab308",
  REFACTOR:   "#f97316",
  RETIRE:     "#94a3b8",
  RETAIN:     "#38bdf8",
  REPURCHASE: "#a855f7",
  RELOCATE:   "#10b981",
};

/* ── Source dependency graph ─────────────────────────────────────────────── */

export function graphToRFNodes(dependencyGraph, sourceCloud) {
  const nodes = dependencyGraph?.resources?.nodes ?? [];
  return nodes.map((n) => ({
    id: n.id,
    type: "service",
    position: { x: 0, y: 0 },
    data: {
      label: n.id,
      type: (n.type ?? "other").toLowerCase(),
      cloud: (n.cloud ?? sourceCloud ?? "unknown").toLowerCase(),
      score: n.score,
      complexity: n.complexity,
      detectionSource: n.source,
      cloudConfirmed: n.cloud_confirmed,
      color: TYPE_COLORS[(n.type ?? "").toLowerCase()] ?? TYPE_COLORS.other,
      cloudColor: CLOUD_COLORS[(n.cloud ?? sourceCloud ?? "").toLowerCase()] ?? "#94a3b8",
      role: "source",
    },
  }));
}

export function graphToRFEdges(dependencyGraph) {
  const edges = dependencyGraph?.resources?.edges ?? [];
  return edges.map((e, i) => ({
    id: `dep-${e.from}-${e.to}-${i}`,
    source: e.from,
    target: e.to,
    type: "smoothstep",
    label: e.relation ?? "",
    animated: false,
    style: { stroke: "#94a3b8", strokeWidth: 1.5 },
    markerEnd: { type: "arrowclosed", color: "#94a3b8" },
    labelStyle: { fontSize: 10, fill: "#64748b" },
    labelBgStyle: { fill: "var(--surface-0, #fff)", fillOpacity: 0.85 },
    labelBgPadding: [4, 6],
    labelBgBorderRadius: 4,
  }));
}

/* ── Migration mapping graph (source → strategy → target) ────────────────── */
/* Kept for backward compat — MigrationGraph.jsx is stubbed but import still present */

export function planToMigrationRF(plan, sourceCloud, targetCloud) {
  const resources = plan?.resources ?? [];
  const sourceNodes = [];
  const targetNodes = [];
  const edges = [];

  resources.forEach((r, i) => {
    const srcId = `src-${i}`;
    const tgtId = `tgt-${i}`;
    const type = (r.type ?? "other").toLowerCase();
    const strategy = (r.strategy ?? "").toUpperCase();
    const retired = ["RETIRE", "RETAIN"].includes(strategy);

    sourceNodes.push({
      id: srcId,
      type: "service",
      position: { x: 0, y: 0 },
      data: {
        label: r.source_service ?? r.service ?? `Service ${i}`,
        type,
        cloud: (sourceCloud ?? "unknown").toLowerCase(),
        score: r.score,
        complexity: r.complexity,
        color: TYPE_COLORS[type] ?? TYPE_COLORS.other,
        cloudColor: CLOUD_COLORS[(sourceCloud ?? "").toLowerCase()] ?? "#94a3b8",
        role: "source",
        resource: r,
      },
    });

    const targetLabel = retired
      ? strategy === "RETIRE" ? "Decommissioned" : "Retained (source)"
      : (r.target_service ?? r.target_equivalent ?? `Target ${i}`);

    targetNodes.push({
      id: tgtId,
      type: "service",
      position: { x: 0, y: 0 },
      data: {
        label: targetLabel,
        type,
        cloud: retired ? (sourceCloud ?? "unknown").toLowerCase() : (targetCloud ?? "unknown").toLowerCase(),
        strategy,
        score: r.composite_score ?? r.score,
        color: retired ? "#94a3b8" : (TYPE_COLORS[type] ?? TYPE_COLORS.other),
        cloudColor: retired
          ? "#94a3b8"
          : (CLOUD_COLORS[(targetCloud ?? "").toLowerCase()] ?? "#94a3b8"),
        role: "target",
        resource: r,
        retired,
      },
    });

    edges.push({
      id: `migration-${i}`,
      source: srcId,
      target: tgtId,
      type: "strategy",
      data: { strategy, color: STRATEGY_COLORS[strategy] ?? "#94a3b8" },
      animated: !retired,
      style: { stroke: STRATEGY_COLORS[strategy] ?? "#94a3b8", strokeWidth: 2 },
      markerEnd: { type: "arrowclosed", color: STRATEGY_COLORS[strategy] ?? "#94a3b8" },
    });
  });

  return { sourceNodes, targetNodes, edges };
}

/* ── Target cloud graph ──────────────────────────────────────────────────── */

export function planToTargetRF(plan, graph, targetCloud) {
  const resources = plan?.resources ?? [];
  const srcEdges = graph?.resources?.edges ?? [];

  // Unique active target services (exclude RETIRE/RETAIN)
  const seen = new Map();
  resources
    .filter((r) => !["RETIRE", "RETAIN"].includes((r.strategy ?? "").toUpperCase()))
    .forEach((r) => {
      const name = r.target_service ?? r.target_equivalent;
      if (name && !seen.has(name)) {
        seen.set(name, r);
      }
    });

  const targetNodes = Array.from(seen.entries()).map(([name, r]) => {
    const type = (r.type ?? "other").toLowerCase();
    return {
      id: `target-${name}`,
      type: "service",
      position: { x: 0, y: 0 },
      data: {
        label: name,
        type,
        cloud: (targetCloud ?? "unknown").toLowerCase(),
        strategy: (r.strategy ?? "").toUpperCase(),
        score: r.composite_score ?? r.score,
        color: TYPE_COLORS[type] ?? TYPE_COLORS.other,
        cloudColor: CLOUD_COLORS[(targetCloud ?? "").toLowerCase()] ?? "#94a3b8",
        role: "target",
        resource: r,
      },
    };
  });

  // Map source service names → target node IDs for edge inference.
  // Register all known aliases (source_service, service, resource_name, service_name)
  // because dependency_graph edges use node IDs which may differ from source_service.
  const srcToTargetId = new Map();
  resources.forEach((r) => {
    const tgt = r.target_service ?? r.target_equivalent;
    if (!tgt || !seen.has(tgt)) return;
    const targetId = `target-${tgt}`;
    const aliases = [r.source_service, r.service, r.resource_name, r.service_name].filter(Boolean);
    aliases.forEach((alias) => { if (!srcToTargetId.has(alias)) srcToTargetId.set(alias, targetId); });
  });

  // Infer target edges from source dependency graph
  const usedEdgeKeys = new Set();
  const targetEdges = srcEdges
    .map((e, i) => {
      const from = srcToTargetId.get(e.from);
      const to = srcToTargetId.get(e.to);
      if (!from || !to || from === to) return null;
      const key = `${from}→${to}`;
      if (usedEdgeKeys.has(key)) return null;
      usedEdgeKeys.add(key);
      return {
        id: `te-${i}`,
        source: from,
        target: to,
        type: "smoothstep",
        animated: true,
        label: e.relation ?? "",
        style: { stroke: "#3b82f6", strokeWidth: 2 },
        markerEnd: { type: "arrowclosed", color: "#3b82f6" },
        labelStyle: { fontSize: 10, fill: "#64748b" },
        labelBgStyle: { fill: "var(--surface-0, #fff)", fillOpacity: 0.9 },
        labelBgPadding: [4, 6],
        labelBgBorderRadius: 4,
      };
    })
    .filter(Boolean);

  // Fallback: if no edges were inferred from source graph, build edges from
  // plan.deployment_order (Agent 01 produces ordered list with implicit deps).
  // Also use plan.dependency_edges if present (explicit edge list from Agent 01).
  if (targetEdges.length === 0) {
    const depEdges = plan?.dependency_edges ?? [];
    depEdges.forEach((e, i) => {
      const from = `target-${e.from ?? e.source}`;
      const to   = `target-${e.to   ?? e.target}`;
      if (!from || !to || from === to) return;
      if (!targetNodes.find((n) => n.id === from) || !targetNodes.find((n) => n.id === to)) return;
      const key = `${from}→${to}`;
      if (usedEdgeKeys.has(key)) return;
      usedEdgeKeys.add(key);
      targetEdges.push({
        id: `dep-edge-${i}`,
        source: from,
        target: to,
        type: "smoothstep",
        animated: true,
        label: e.relation ?? "",
        style: { stroke: "#3b82f6", strokeWidth: 2 },
        markerEnd: { type: "arrowclosed", color: "#3b82f6" },
        labelStyle: { fontSize: 10, fill: "#64748b" },
        labelBgStyle: { fill: "var(--surface-0, #fff)", fillOpacity: 0.9 },
        labelBgPadding: [4, 6],
        labelBgBorderRadius: 4,
      });
    });

    // Last resort: deployment_order is an ordered array — create sequential edges.
    if (targetEdges.length === 0) {
      const order = plan?.deployment_order ?? [];
      const nodeIds = targetNodes.map((n) => n.id);
      // deployment_order may be string names or objects with a name field
      const orderIds = order
        .map((item) => {
          const name = typeof item === "string" ? item : (item?.name ?? item?.service ?? item?.target_service ?? "");
          // Match against target node IDs (target-<name>)
          const direct = `target-${name}`;
          if (nodeIds.includes(direct)) return direct;
          // Fuzzy: find node whose label contains the order item
          const match = targetNodes.find((n) =>
            n.data.label.toLowerCase().includes(name.toLowerCase()) ||
            name.toLowerCase().includes(n.data.label.toLowerCase())
          );
          return match?.id ?? null;
        })
        .filter(Boolean);

      for (let i = 0; i < orderIds.length - 1; i++) {
        const from = orderIds[i];
        const to   = orderIds[i + 1];
        if (!from || !to || from === to) continue;
        const key = `${from}→${to}`;
        if (usedEdgeKeys.has(key)) continue;
        usedEdgeKeys.add(key);
        targetEdges.push({
          id: `order-${i}`,
          source: from,
          target: to,
          type: "smoothstep",
          animated: true,
          label: "depends on",
          style: { stroke: "#6366f1", strokeWidth: 1.5, strokeDasharray: "5 3" },
          markerEnd: { type: "arrowclosed", color: "#6366f1" },
          labelStyle: { fontSize: 9, fill: "#6366f1" },
          labelBgStyle: { fill: "var(--surface-0, #fff)", fillOpacity: 0.85 },
          labelBgPadding: [3, 5],
          labelBgBorderRadius: 4,
        });
      }
    }
  }

  return { targetNodes, targetEdges };
}
