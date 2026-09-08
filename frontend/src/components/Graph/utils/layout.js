/**
 * Graph layout utilities for React Flow.
 * All functions return nodes with populated `position: { x, y }`.
 * No external layout library — pure JS geometry.
 */

const NODE_W = 190;
const NODE_H = 84;
const COL_GAP = 200;  // horizontal gap between layers (180–240px spec)
const ROW_GAP = 120;  // vertical gap between nodes (min 120px spec)
const PAD = 60;

/* ── Grouped layout ─────────────────────────────────────────────────────────
   Groups nodes by a key (e.g. "type"), arranges each group as a vertical
   column. Useful for target-cloud views.
 */
export function groupedLayout(nodes, groupKey = "type") {
  const groups = new Map();
  nodes.forEach((n) => {
    const g = (n.data?.[groupKey] ?? n[groupKey] ?? "other").toLowerCase();
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(n);
  });

  const result = [];
  let col = 0;
  for (const groupNodes of groups.values()) {
    groupNodes.forEach((n, row) => {
      result.push({
        ...n,
        position: {
          x: PAD + col * (NODE_W + COL_GAP),
          y: PAD + row * (NODE_H + ROW_GAP),
        },
      });
    });
    col++;
  }
  return result;
}

/* ── DAG layout ─────────────────────────────────────────────────────────────
   BFS from root nodes (no incoming edges) to assign horizontal layers.
   Nodes in each layer are stacked vertically, centered.
   Works well for dependency graphs. Left-to-right direction.
 */
export function dagLayout(nodes, edges) {
  if (!nodes.length) return nodes;

  const inDegree = new Map(nodes.map((n) => [n.id, 0]));
  edges.forEach((e) => {
    if (inDegree.has(e.target)) inDegree.set(e.target, inDegree.get(e.target) + 1);
  });

  const layer = new Map();
  const queue = nodes.filter((n) => inDegree.get(n.id) === 0).map((n) => ({ id: n.id, l: 0 }));
  const visited = new Set();

  while (queue.length > 0) {
    const { id, l } = queue.shift();
    if (visited.has(id)) continue;
    visited.add(id);
    layer.set(id, Math.max(layer.get(id) ?? 0, l));

    edges
      .filter((e) => e.source === id)
      .forEach((e) => {
        if (!visited.has(e.target)) queue.push({ id: e.target, l: l + 1 });
      });
  }

  // Assign disconnected nodes to a final layer
  const maxLayer = layer.size ? Math.max(...layer.values()) : 0;
  nodes.forEach((n) => {
    if (!layer.has(n.id)) layer.set(n.id, maxLayer + 1);
  });

  // Group by layer
  const layers = new Map();
  layer.forEach((l, id) => {
    if (!layers.has(l)) layers.set(l, []);
    layers.get(l).push(id);
  });

  // Compute canvas height for vertical centering
  const maxColSize = Math.max(...Array.from(layers.values(), (ids) => ids.length));
  const canvasH = PAD * 2 + maxColSize * (NODE_H + ROW_GAP) - ROW_GAP;

  const positions = new Map();
  layers.forEach((ids, l) => {
    const colH = ids.length * (NODE_H + ROW_GAP) - ROW_GAP;
    const startY = Math.round((canvasH - colH) / 2);
    ids.forEach((id, i) => {
      positions.set(id, {
        x: PAD + l * (NODE_W + COL_GAP),
        y: startY + i * (NODE_H + ROW_GAP),
      });
    });
  });

  return nodes.map((n) => ({
    ...n,
    position: positions.get(n.id) ?? { x: PAD, y: PAD },
  }));
}

/* ── Two-column layout ───────────────────────────────────────────────────────
   Left column = source nodes, right column = target nodes.
   A wide central gap leaves room for the strategy edge labels.
 */
export function twoColumnLayout(leftNodes, rightNodes) {
  const rightX = PAD + NODE_W + 320;

  return [
    ...leftNodes.map((n, i) => ({
      ...n,
      position: { x: PAD, y: PAD + i * (NODE_H + ROW_GAP) },
    })),
    ...rightNodes.map((n, i) => ({
      ...n,
      position: { x: rightX, y: PAD + i * (NODE_H + ROW_GAP) },
    })),
  ];
}
