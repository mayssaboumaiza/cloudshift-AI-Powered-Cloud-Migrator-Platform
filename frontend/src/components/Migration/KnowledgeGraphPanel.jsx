/**
 * KnowledgeGraphPanel.jsx — Précise Knowledge Graph Neo4j viewer.
 *
 * Two views:
 *  1. "Provider Graph" — top N most-connected resources per cloud provider.
 *     Nodes show resource names, edges show relationship types with labels.
 *  2. "Migration Graph" — source cloud resources → target cloud resources.
 *     Migration arrows (MIGRATES_TO) with strategy + score.
 *     Internal RELATED_TO edges inside each cloud.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import {
  Network, Database, ZoomIn, ZoomOut, Maximize2, RefreshCw,
  GitBranch, Layers, X, ChevronDown, Info, ArrowRight, ExternalLink,
} from "lucide-react";

// ── Color system ──────────────────────────────────────────────────────────────
const PROVIDER = {
  aws:     { color: "#fb923c", dark: "#7c2d12", label: "AWS",   text: "#fed7aa" },
  azurerm: { color: "#38bdf8", dark: "#0c4a6e", label: "Azure", text: "#bae6fd" },
  azure:   { color: "#38bdf8", dark: "#0c4a6e", label: "Azure", text: "#bae6fd" },
  google:  { color: "#4ade80", dark: "#14532d", label: "GCP",   text: "#bbf7d0" },
  gcp:     { color: "#4ade80", dark: "#14532d", label: "GCP",   text: "#bbf7d0" },
  other:   { color: "#a78bfa", dark: "#2e1065", label: "?",     text: "#ddd6fe" },
};
const REL = {
  RELATED_TO:   { color: "#64748b", dash: "none",  width: 1 },
  REFERENCES:   { color: "#fbbf24", dash: "6 3",   width: 1.5 },
  HAS_ARGUMENT: { color: "#34d399", dash: "3 3",   width: 1 },
  IN_COMMUNITY: { color: "#a78bfa", dash: "2 4",   width: 1 },
  COMPANION:    { color: "#fb923c", dash: "none",  width: 2 },
  MIGRATES_TO:  { color: "#f472b6", dash: "none",  width: 2.5 },
};
const STRATEGY = {
  REHOST:      { color: "#22c55e", label: "Rehost" },
  REPLATFORM:  { color: "#3b82f6", label: "Replatform" },
  REFACTOR:    { color: "#f59e0b", label: "Refactor" },
  RETIRE:      { color: "#ef4444", label: "Retire" },
  RETAIN:      { color: "#94a3b8", label: "Retain" },
  REPURCHASE:  { color: "#8b5cf6", label: "Repurchase" },
  RELOCATE:    { color: "#06b6d4", label: "Relocate" },
};

function providerOf(id = "") {
  const l = id.toLowerCase();
  if (l.startsWith("aws_") || l === "aws") return "aws";
  if (l.startsWith("azurerm_")) return "azurerm";
  if (l.startsWith("google_")) return "google";
  return "other";
}
function ps(id) { return PROVIDER[providerOf(id)] || PROVIDER.other; }
function shortLabel(id = "") {
  // aws_db_instance → db_instance, azurerm_postgresql_flexible_server → postgresql_flexible_server
  const parts = id.split("_");
  if (parts.length > 2) return parts.slice(1).join("_");
  return id;
}
function serviceOf(id = "") {
  const parts = id.split("_");
  return parts.length > 2 ? parts[1] : parts[parts.length - 1];
}

// ── Layout helpers ────────────────────────────────────────────────────────────
function layoutProviderGraph(nodes) {
  // Group by service category, arrange in concentric rings per service
  const byService = {};
  for (const n of nodes) {
    const svc = serviceOf(n.id);
    if (!byService[svc]) byService[svc] = [];
    byService[svc].push(n);
  }
  const services = Object.keys(byService);
  const W = 1000, H = 700;
  const cx = W / 2, cy = H / 2;
  const positions = {};

  services.forEach((svc, si) => {
    const list = byService[svc];
    const svcAngle = (2 * Math.PI / services.length) * si - Math.PI / 2;
    const svcR = Math.min(W, H) * 0.36;
    const scx = cx + svcR * Math.cos(svcAngle);
    const scy = cy + svcR * Math.sin(svcAngle);
    const nodeR = Math.min(55, 180 / Math.max(list.length, 1));
    list.forEach((n, ni) => {
      const angle = list.length === 1 ? 0 : (2 * Math.PI / list.length) * ni - Math.PI / 2;
      positions[n.id] = {
        x: scx + (list.length === 1 ? 0 : nodeR * Math.cos(angle)),
        y: scy + (list.length === 1 ? 0 : nodeR * Math.sin(angle)),
        svcX: scx, svcY: scy,
        svc,
      };
    });
  });
  return { positions, byService };
}

function layoutMigrationGraph(sourceNodes, targetNodes) {
  const W = 1000, H = 600;
  const positions = {};
  // Source on left, target on right
  const srcX = W * 0.22, tgtX = W * 0.78;
  const layoutSide = (nodes, cx) => {
    const n = nodes.length;
    nodes.forEach((node, i) => {
      const y = n === 1 ? H / 2 : H * 0.15 + (H * 0.70 / (n - 1)) * i;
      positions[node.id] = { x: cx, y };
    });
  };
  layoutSide(sourceNodes, srcX);
  layoutSide(targetNodes, tgtX);
  return positions;
}

// ── Detail panels ─────────────────────────────────────────────────────────────
function NodePanel({ node, onClose }) {
  if (!node) return null;
  const s = ps(node.id);
  return (
    <div style={{
      position: "absolute", top: 10, right: 10, width: 290, zIndex: 20,
      background: "#1e293b", border: `1px solid ${s.color}55`,
      borderRadius: 10, padding: "14px 16px",
      boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 8 }}>
        <span style={{ width: 10, height: 10, borderRadius: "50%", background: s.color, flexShrink: 0, marginTop: 3 }} />
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: "monospace", fontSize: 11, color: s.color, fontWeight: 700, wordBreak: "break-all" }}>
            {node.id}
          </div>
          <div style={{ fontSize: 10, color: "#475569", marginTop: 2 }}>{s.label} · {serviceOf(node.id)}</div>
        </div>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: "#475569", padding: 2 }}>
          <X size={13} />
        </button>
      </div>

      {node.description && (
        <p style={{ fontSize: 11, color: "#94a3b8", margin: "0 0 10px", lineHeight: 1.5 }}>
          {node.description.slice(0, 200)}{node.description.length > 200 ? "…" : ""}
        </p>
      )}

      {node.required_args?.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 5 }}>
            Arguments requis
          </div>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {node.required_args.map((a, i) => (
              <code key={i} style={{ padding: "1px 6px", background: "#0f172a", borderRadius: 4, fontSize: 10, color: "#cbd5e1", border: "1px solid #334155" }}>
                {a}
              </code>
            ))}
          </div>
        </div>
      )}

      {/* Migration info */}
      {node.strategy && (
        <div style={{
          padding: "8px 10px", background: "#0f172a", borderRadius: 6, fontSize: 11,
          border: `1px solid ${(STRATEGY[node.strategy] || STRATEGY.REHOST).color}44`,
        }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 4 }}>
            <span style={{ padding: "1px 8px", borderRadius: 10, fontSize: 10, fontWeight: 700, background: `${(STRATEGY[node.strategy] || STRATEGY.REHOST).color}22`, color: (STRATEGY[node.strategy] || STRATEGY.REHOST).color }}>
              {node.strategy}
            </span>
            <span style={{ color: "#64748b", fontSize: 10 }}>Score: {(node.score * 100).toFixed(0)}%</span>
          </div>
          {node.effort_days > 0 && <div style={{ fontSize: 10, color: "#94a3b8" }}>{node.effort_days}j effort · {node.monthly_cost_eur}€/mo</div>}
        </div>
      )}

      {node.degree != null && (
        <div style={{ fontSize: 10, color: "#475569", marginTop: 6 }}>
          {node.degree} relations dans la base de connaissance
        </div>
      )}
    </div>
  );
}

function EdgePanel({ edge, onClose }) {
  if (!edge) return null;
  const rel = REL[edge.type] || REL.RELATED_TO;
  const stratInfo = edge.strategy ? STRATEGY[edge.strategy] || STRATEGY.REHOST : null;
  return (
    <div style={{
      position: "absolute", bottom: 10, right: 10, width: 270, zIndex: 20,
      background: "#1e293b", border: `1px solid ${rel.color}55`,
      borderRadius: 10, padding: "12px 14px",
      boxShadow: "0 8px 32px rgba(0,0,0,0.6)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={{ padding: "2px 10px", borderRadius: 20, fontSize: 10, fontWeight: 800, background: `${rel.color}22`, color: rel.color, border: `1px solid ${rel.color}44` }}>
          {edge.type}
        </span>
        <button onClick={onClose} style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "#475569" }}>
          <X size={13} />
        </button>
      </div>
      <div style={{ fontSize: 11, color: "#64748b", display: "flex", flexDirection: "column", gap: 4 }}>
        <code style={{ color: ps(edge.source).color, fontSize: 10, wordBreak: "break-all" }}>{edge.source}</code>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ flex: 1, height: 1, background: rel.color + "66" }} />
          <ArrowRight size={10} color={rel.color} />
          <span style={{ flex: 1, height: 1, background: rel.color + "66" }} />
        </div>
        <code style={{ color: ps(edge.target).color, fontSize: 10, wordBreak: "break-all" }}>{edge.target}</code>
      </div>
      {stratInfo && (
        <div style={{ marginTop: 8, display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ padding: "1px 8px", borderRadius: 10, fontSize: 10, fontWeight: 700, background: `${stratInfo.color}22`, color: stratInfo.color }}>
            {edge.strategy}
          </span>
          {edge.score != null && <span style={{ fontSize: 10, color: "#64748b" }}>Score {(edge.score * 100).toFixed(0)}%</span>}
        </div>
      )}
    </div>
  );
}

// ── Provider graph SVG ────────────────────────────────────────────────────────
function ProviderGraphCanvas({ nodes, edges, onNodeClick, onEdgeClick, selectedNode, selectedEdge }) {
  const svgRef = useRef(null);
  const [pan, setPan]   = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(0.85);
  const [drag, setDrag] = useState(null);
  const W = 1000, H = 700;

  const { positions, byService } = layoutProviderGraph(nodes);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const handler = (e) => { e.preventDefault(); setZoom(z => Math.max(0.2, Math.min(4, z * (e.deltaY > 0 ? 0.9 : 1.1)))); };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, []);

  const onMD = (e) => {
    if (e.target.closest(".gnode,.gedge")) return;
    setDrag({ sx: e.clientX - pan.x, sy: e.clientY - pan.y });
  };
  const onMM = (e) => { if (drag) setPan({ x: e.clientX - drag.sx, y: e.clientY - drag.sy }); };
  const onMU = () => setDrag(null);

  return (
    <div style={{ position: "relative", width: "100%", height: 500, background: "#0a0f1a", borderRadius: 10, overflow: "hidden", border: "1px solid #1e293b" }}>
      {/* Zoom controls */}
      <div style={{ position: "absolute", top: 10, left: 10, zIndex: 5, display: "flex", flexDirection: "column", gap: 4 }}>
        {[
          { icon: <ZoomIn size={12} />, fn: () => setZoom(z => Math.min(4, z * 1.2)) },
          { icon: <ZoomOut size={12} />, fn: () => setZoom(z => Math.max(0.2, z * 0.8)) },
          { icon: <Maximize2 size={12} />, fn: () => { setZoom(0.85); setPan({ x: 0, y: 0 }); } },
        ].map((b, i) => (
          <button key={i} onClick={b.fn} style={{
            width: 26, height: 26, background: "#1e293b", border: "1px solid #334155",
            borderRadius: 6, cursor: "pointer", color: "#94a3b8",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>{b.icon}</button>
        ))}
      </div>

      <svg ref={svgRef} width="100%" height="100%" viewBox={`0 0 ${W} ${H}`}
        style={{ cursor: drag ? "grabbing" : "grab" }}
        onMouseDown={onMD} onMouseMove={onMM} onMouseUp={onMU} onMouseLeave={onMU}
      >
        <defs>
          {Object.entries(REL).map(([t, r]) => (
            <marker key={t} id={`arr-${t}`} viewBox="0 0 8 8" refX="7" refY="2.5"
              markerWidth={5} markerHeight={5} orient="auto">
              <path d="M0,0 L0,5 L8,2.5 z" fill={r.color} opacity={0.7} />
            </marker>
          ))}
        </defs>

        <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
          {/* Service cluster halos */}
          {Object.entries(layoutProviderGraph(nodes).byService).map(([svc, list]) => {
            const first = positions[list[0]?.id];
            if (!first) return null;
            const s = ps(list[0]?.id || "");
            const r = list.length <= 1 ? 24 : Math.min(90, 18 + list.length * 10);
            return (
              <g key={svc}>
                <circle cx={first.svcX} cy={first.svcY} r={r}
                  fill={`${s.color}07`} stroke={`${s.color}18`} strokeWidth={1} strokeDasharray="4 3" />
                <text x={first.svcX} y={first.svcY - r - 4} textAnchor="middle"
                  fill={s.color} fontSize={8.5} fontWeight={600} opacity={0.6}>
                  {svc}
                </text>
              </g>
            );
          })}

          {/* Edges */}
          {edges.map((e, i) => {
            const sp = positions[e.source], tp = positions[e.target];
            if (!sp || !tp) return null;
            const rel = REL[e.type] || REL.RELATED_TO;
            const isSel = selectedEdge?.source === e.source && selectedEdge?.target === e.target;
            // Curved edge to avoid overlap
            const mx = (sp.x + tp.x) / 2, my = (sp.y + tp.y) / 2;
            const dx = tp.x - sp.x, dy = tp.y - sp.y;
            const len = Math.sqrt(dx * dx + dy * dy) || 1;
            const curve = Math.min(40, len * 0.15);
            const cx2 = mx - (dy / len) * curve, cy2 = my + (dx / len) * curve;
            return (
              <g key={i} className="gedge" onClick={() => onEdgeClick(e)} style={{ cursor: "pointer" }}>
                <path d={`M${sp.x},${sp.y} Q${cx2},${cy2} ${tp.x},${tp.y}`}
                  fill="none" stroke={rel.color}
                  strokeWidth={isSel ? rel.width * 2.5 : rel.width}
                  strokeOpacity={isSel ? 1 : 0.35}
                  strokeDasharray={rel.dash}
                  markerEnd={`url(#arr-${e.type})`}
                />
                {/* Wide invisible hit area */}
                <path d={`M${sp.x},${sp.y} Q${cx2},${cy2} ${tp.x},${tp.y}`}
                  fill="none" stroke="transparent" strokeWidth={10} />
                {/* Edge label at midpoint */}
                {isSel && (
                  <text x={cx2} y={cy2 - 4} textAnchor="middle" fontSize={8} fill={rel.color} fontWeight={700}>
                    {e.type}
                  </text>
                )}
              </g>
            );
          })}

          {/* Nodes */}
          {nodes.map((n) => {
            const pos = positions[n.id];
            if (!pos) return null;
            const s = ps(n.id);
            const isSel = selectedNode?.id === n.id;
            const r = isSel ? 10 : 7;
            const lbl = shortLabel(n.id);
            return (
              <g key={n.id} className="gnode" onClick={() => onNodeClick(n)} style={{ cursor: "pointer" }}>
                {isSel && <circle cx={pos.x} cy={pos.y} r={r + 5} fill="none" stroke={s.color} strokeWidth={1.5} opacity={0.5} />}
                <circle cx={pos.x} cy={pos.y} r={r}
                  fill={s.color} stroke="#0a0f1a" strokeWidth={isSel ? 2 : 1}
                  opacity={isSel ? 1 : 0.85}
                />
                <text x={pos.x} y={pos.y + r + 10} textAnchor="middle"
                  fontSize={isSel ? 8.5 : 7} fontWeight={isSel ? 700 : 400}
                  fill={s.color} opacity={isSel ? 1 : 0.75}>
                  {lbl.length > 22 ? lbl.slice(0, 20) + "…" : lbl}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

// ── Migration graph SVG ───────────────────────────────────────────────────────
function MigrationGraphCanvas({ data, onNodeClick, onEdgeClick, selectedNode, selectedEdge }) {
  const svgRef = useRef(null);
  const [pan, setPan]   = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [drag, setDrag] = useState(null);
  const W = 1000, H = 600;

  const { source_nodes = [], target_nodes = [], migration_edges = [], internal_edges = [], target_edges = [] } = data;
  const positions = layoutMigrationGraph(source_nodes, target_nodes);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const h = (e) => { e.preventDefault(); setZoom(z => Math.max(0.2, Math.min(4, z * (e.deltaY > 0 ? 0.9 : 1.1)))); };
    el.addEventListener("wheel", h, { passive: false });
    return () => el.removeEventListener("wheel", h);
  }, []);

  const onMD = (e) => {
    if (e.target.closest(".gnode,.gedge")) return;
    setDrag({ sx: e.clientX - pan.x, sy: e.clientY - pan.y });
  };
  const onMM = (e) => { if (drag) setPan({ x: e.clientX - drag.sx, y: e.clientY - drag.sy }); };
  const onMU = () => setDrag(null);

  const srcColor  = PROVIDER[data.source_cloud] || PROVIDER.aws;
  const tgtColor  = PROVIDER[data.target_cloud === "azure" ? "azurerm" : (data.target_cloud || "azurerm")] || PROVIDER.azurerm;

  return (
    <div style={{ position: "relative", width: "100%", height: 500, background: "#0a0f1a", borderRadius: 10, overflow: "hidden", border: "1px solid #1e293b" }}>
      <div style={{ position: "absolute", top: 10, left: 10, zIndex: 5, display: "flex", flexDirection: "column", gap: 4 }}>
        {[
          { icon: <ZoomIn size={12} />, fn: () => setZoom(z => Math.min(4, z * 1.2)) },
          { icon: <ZoomOut size={12} />, fn: () => setZoom(z => Math.max(0.2, z * 0.8)) },
          { icon: <Maximize2 size={12} />, fn: () => { setZoom(1); setPan({ x: 0, y: 0 }); } },
        ].map((b, i) => (
          <button key={i} onClick={b.fn} style={{ width: 26, height: 26, background: "#1e293b", border: "1px solid #334155", borderRadius: 6, cursor: "pointer", color: "#94a3b8", display: "flex", alignItems: "center", justifyContent: "center" }}>{b.icon}</button>
        ))}
      </div>

      <svg ref={svgRef} width="100%" height="100%" viewBox={`0 0 ${W} ${H}`}
        style={{ cursor: drag ? "grabbing" : "grab" }}
        onMouseDown={onMD} onMouseMove={onMM} onMouseUp={onMU} onMouseLeave={onMU}
      >
        <defs>
          {[srcColor.color, tgtColor.color, "#f472b6", "#64748b", "#fbbf24"].map((c, i) => (
            <marker key={i} id={`marr-${i}`} viewBox="0 0 8 8" refX="7" refY="2.5"
              markerWidth={5} markerHeight={5} orient="auto">
              <path d="M0,0 L0,5 L8,2.5 z" fill={c} opacity={0.8} />
            </marker>
          ))}
        </defs>

        <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
          {/* Cloud zone backgrounds */}
          <rect x={W * 0.04} y={H * 0.06} width={W * 0.35} height={H * 0.88}
            fill={`${srcColor.color}06`} stroke={`${srcColor.color}20`} strokeWidth={1} rx={12} />
          <text x={W * 0.215} y={H * 0.045} textAnchor="middle" fontSize={12} fontWeight={700} fill={srcColor.color} opacity={0.8}>
            {srcColor.label} (source)
          </text>

          <rect x={W * 0.6} y={H * 0.06} width={W * 0.36} height={H * 0.88}
            fill={`${tgtColor.color}06`} stroke={`${tgtColor.color}20`} strokeWidth={1} rx={12} />
          <text x={W * 0.78} y={H * 0.045} textAnchor="middle" fontSize={12} fontWeight={700} fill={tgtColor.color} opacity={0.8}>
            {tgtColor.label} (cible)
          </text>

          {/* Internal edges — source side */}
          {internal_edges.map((e, i) => {
            const sp = positions[e.source], tp = positions[e.target];
            if (!sp || !tp) return null;
            const rel = REL[e.type] || REL.RELATED_TO;
            const isSel = selectedEdge?.source === e.source && selectedEdge?.target === e.target;
            const curve = 30;
            const mx = (sp.x + tp.x) / 2 - curve;
            const my = (sp.y + tp.y) / 2;
            return (
              <g key={`ie-${i}`} className="gedge" onClick={() => onEdgeClick({ ...e, type: e.type })} style={{ cursor: "pointer" }}>
                <path d={`M${sp.x},${sp.y} Q${mx},${my} ${tp.x},${tp.y}`}
                  fill="none" stroke={rel.color} strokeWidth={isSel ? 2 : 1}
                  strokeOpacity={isSel ? 0.9 : 0.3} strokeDasharray={rel.dash}
                  markerEnd="url(#marr-3)" />
                <path d={`M${sp.x},${sp.y} Q${mx},${my} ${tp.x},${tp.y}`}
                  fill="none" stroke="transparent" strokeWidth={10} />
                {isSel && (
                  <text x={mx} y={my - 4} textAnchor="middle" fontSize={8} fill={rel.color} fontWeight={700}>{e.type}</text>
                )}
              </g>
            );
          })}

          {/* Target internal edges */}
          {target_edges.map((e, i) => {
            const sp = positions[e.source], tp = positions[e.target];
            if (!sp || !tp) return null;
            const rel = REL[e.type] || REL.RELATED_TO;
            const isSel = selectedEdge?.source === e.source && selectedEdge?.target === e.target;
            const mx = (sp.x + tp.x) / 2 + 30;
            const my = (sp.y + tp.y) / 2;
            return (
              <g key={`te-${i}`} className="gedge" onClick={() => onEdgeClick({ ...e })} style={{ cursor: "pointer" }}>
                <path d={`M${sp.x},${sp.y} Q${mx},${my} ${tp.x},${tp.y}`}
                  fill="none" stroke={rel.color} strokeWidth={isSel ? 2 : 1}
                  strokeOpacity={isSel ? 0.9 : 0.3} strokeDasharray={rel.dash}
                  markerEnd="url(#marr-3)" />
                <path d={`M${sp.x},${sp.y} Q${mx},${my} ${tp.x},${tp.y}`}
                  fill="none" stroke="transparent" strokeWidth={10} />
              </g>
            );
          })}

          {/* Migration arrows: source → target */}
          {migration_edges.map((m, i) => {
            const sp = positions[m.source], tp = positions[m.target];
            if (!sp || !tp) return null;
            const strat = STRATEGY[m.strategy] || STRATEGY.REHOST;
            const isSel = selectedEdge?.source === m.source && selectedEdge?.target === m.target;
            const mpy = (sp.y + tp.y) / 2;
            return (
              <g key={`me-${i}`} className="gedge"
                onClick={() => onEdgeClick({ source: m.source, target: m.target, type: "MIGRATES_TO", strategy: m.strategy, score: m.score })}
                style={{ cursor: "pointer" }}>
                <line x1={sp.x} y1={sp.y} x2={tp.x} y2={tp.y}
                  stroke={strat.color}
                  strokeWidth={isSel ? 3 : 2}
                  strokeOpacity={isSel ? 1 : 0.6}
                  markerEnd="url(#marr-2)"
                />
                <line x1={sp.x} y1={sp.y} x2={tp.x} y2={tp.y}
                  stroke="transparent" strokeWidth={12} />
                {/* Strategy badge at midpoint */}
                <rect x={(sp.x + tp.x) / 2 - 28} y={mpy - 8} width={56} height={14} rx={7}
                  fill={`${strat.color}22`} stroke={`${strat.color}66`} strokeWidth={0.5} />
                <text x={(sp.x + tp.x) / 2} y={mpy + 3.5} textAnchor="middle"
                  fontSize={7.5} fontWeight={700} fill={strat.color}>
                  {m.strategy} {m.score ? `${(m.score * 100).toFixed(0)}%` : ""}
                </text>
              </g>
            );
          })}

          {/* Source nodes */}
          {source_nodes.map((n) => {
            const pos = positions[n.id];
            if (!pos) return null;
            const isSel = selectedNode?.id === n.id;
            return (
              <g key={n.id} className="gnode" onClick={() => onNodeClick(n)} style={{ cursor: "pointer" }}>
                {isSel && <circle cx={pos.x} cy={pos.y} r={18} fill="none" stroke={srcColor.color} strokeWidth={1.5} opacity={0.4} strokeDasharray="3 2" />}
                <circle cx={pos.x} cy={pos.y} r={12} fill={srcColor.color} stroke="#0a0f1a" strokeWidth={isSel ? 2 : 1} />
                <text x={pos.x} y={pos.y + 5} textAnchor="middle" fontSize={9} fill="#0a0f1a" fontWeight={800}>
                  {serviceOf(n.id).slice(0, 3).toUpperCase()}
                </text>
                <text x={pos.x + 16} y={pos.y - 2} fontSize={9} fontWeight={700} fill={srcColor.color}>
                  {shortLabel(n.id).length > 28 ? shortLabel(n.id).slice(0, 26) + "…" : shortLabel(n.id)}
                </text>
                <text x={pos.x + 16} y={pos.y + 10} fontSize={7.5} fill="#64748b">
                  {n.provider || data.source_cloud}
                </text>
              </g>
            );
          })}

          {/* Target nodes */}
          {target_nodes.map((n) => {
            const pos = positions[n.id];
            if (!pos) return null;
            const isSel = selectedNode?.id === n.id;
            return (
              <g key={n.id} className="gnode" onClick={() => onNodeClick(n)} style={{ cursor: "pointer" }}>
                {isSel && <circle cx={pos.x} cy={pos.y} r={18} fill="none" stroke={tgtColor.color} strokeWidth={1.5} opacity={0.4} strokeDasharray="3 2" />}
                <circle cx={pos.x} cy={pos.y} r={12} fill={tgtColor.color} stroke="#0a0f1a" strokeWidth={isSel ? 2 : 1} />
                <text x={pos.x} y={pos.y + 5} textAnchor="middle" fontSize={9} fill="#0a0f1a" fontWeight={800}>
                  {serviceOf(n.id).slice(0, 3).toUpperCase()}
                </text>
                <text x={pos.x - 16} y={pos.y - 2} fontSize={9} fontWeight={700} fill={tgtColor.color} textAnchor="end">
                  {shortLabel(n.id).length > 28 ? shortLabel(n.id).slice(0, 26) + "…" : shortLabel(n.id)}
                </text>
                <text x={pos.x - 16} y={pos.y + 10} fontSize={7.5} fill="#64748b" textAnchor="end">
                  {n.provider || data.target_cloud}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
const PROVIDERS = ["aws", "azurerm", "google"];

export default function KnowledgeGraphPanel({ migrationId, migrationPlan }) {
  const [view, setView]             = useState("migration"); // "migration" | "aws" | "azurerm" | "google"
  const [providerData, setProvData] = useState({});         // cache by provider
  const [migData, setMigData]       = useState(null);
  const [loading, setLoading]       = useState(false);
  const [error, setError]           = useState(null);
  const [selectedNode, setSN]       = useState(null);
  const [selectedEdge, setSE]       = useState(null);
  const [topN, setTopN]             = useState(50);

  const hasMigration = !!migrationId;

  const fetchProvider = useCallback(async (provider) => {
    if (providerData[`${provider}-${topN}`]) return;
    setLoading(true); setError(null);
    try {
      const res = await fetch(`/api/v1/graph-rag/provider-graph?provider=${provider}&top_n=${topN}`);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      setProvData(prev => ({ ...prev, [`${provider}-${topN}`]: data }));
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [providerData, topN]);

  const fetchMigration = useCallback(async () => {
    if (!migrationId) return;
    setLoading(true); setError(null);
    try {
      const res = await fetch(`/api/v1/graph-rag/migration-graph/${migrationId}`);
      if (!res.ok) throw new Error(`${res.status}`);
      setMigData(await res.json());
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [migrationId]);

  useEffect(() => {
    setSN(null); setSE(null);
    if (view === "migration") fetchMigration();
    else fetchProvider(view);
  }, [view, topN]);

  const currentData = view === "migration" ? null : providerData[`${view}-${topN}`];
  const isLoading   = loading && (view === "migration" ? !migData : !currentData);

  const stats = view === "migration" && migData
    ? { nodes: (migData.source_nodes?.length || 0) + (migData.target_nodes?.length || 0), edges: migData.migration_edges?.length || 0 }
    : currentData ? { nodes: currentData.nodes?.length || 0, edges: currentData.edges?.length || 0 }
    : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Neo4j Browser link */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 14px",
        background: "linear-gradient(135deg,rgba(22,163,74,0.06),rgba(34,211,153,0.06))",
        border: "1px solid rgba(34,211,153,0.25)", borderRadius: 10, fontSize: 12,
      }}>
        <Network size={15} color="#16a34a" />
        <div style={{ flex: 1 }}>
          <span style={{ fontWeight: 700, color: "#15803d" }}>Neo4j Browser</span>
          <span style={{ color: "var(--text-tertiary)", marginLeft: 8 }}>
            3 859 ressources · 28 293 arguments · 168 communautés
          </span>
        </div>
        <a
          href="http://localhost:7474"
          target="_blank"
          rel="noreferrer"
          style={{
            display: "flex", alignItems: "center", gap: 5,
            padding: "5px 14px", borderRadius: 7,
            background: "#16a34a", color: "#fff",
            fontWeight: 700, fontSize: 11, textDecoration: "none", flexShrink: 0,
          }}
        >
          Ouvrir Neo4j Browser ↗
        </a>
        <div style={{ fontSize: 10, color: "var(--text-disabled)", flexShrink: 0 }}>
          neo4j / cloud-migrator
        </div>
      </div>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Network size={15} style={{ color: "#60a5fa" }} />
          <span style={{ fontWeight: 700, fontSize: 13 }}>Knowledge Graph Neo4j</span>
        </div>

        {stats && (
          <div style={{ display: "flex", gap: 6 }}>
            <span style={{ padding: "2px 8px", background: "#1e293b", border: "1px solid #334155", borderRadius: 20, fontSize: 11, color: "#94a3b8" }}>
              <strong style={{ color: "#e2e8f0" }}>{stats.nodes}</strong> nœuds
            </span>
            <span style={{ padding: "2px 8px", background: "#1e293b", border: "1px solid #334155", borderRadius: 20, fontSize: 11, color: "#94a3b8" }}>
              <strong style={{ color: "#e2e8f0" }}>{stats.edges}</strong> relations
            </span>
          </div>
        )}

        {/* View selector */}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4, flexWrap: "wrap" }}>
          {hasMigration && (
            <button onClick={() => setView("migration")} style={{
              padding: "4px 12px", fontSize: 11, fontWeight: 600, borderRadius: 6, cursor: "pointer",
              background: view === "migration" ? "#f472b6" : "#1e293b",
              color: view === "migration" ? "#fff" : "#94a3b8",
              border: `1px solid ${view === "migration" ? "#f472b6" : "#334155"}`,
            }}>
              🔄 Migration
            </button>
          )}
          {PROVIDERS.map(p => {
            const s = PROVIDER[p] || PROVIDER.other;
            return (
              <button key={p} onClick={() => setView(p)} style={{
                padding: "4px 12px", fontSize: 11, fontWeight: 600, borderRadius: 6, cursor: "pointer",
                background: view === p ? s.color : "#1e293b",
                color: view === p ? "#0a0f1a" : "#94a3b8",
                border: `1px solid ${view === p ? s.color : "#334155"}`,
              }}>
                {s.label}
              </button>
            );
          })}
          {view !== "migration" && (
            <select value={topN} onChange={e => { setTopN(+e.target.value); setProvData({}); }} style={{
              padding: "4px 8px", fontSize: 11, background: "#1e293b", color: "#94a3b8",
              border: "1px solid #334155", borderRadius: 6, cursor: "pointer",
            }}>
              {[30, 50, 80, 120].map(n => <option key={n} value={n}>Top {n}</option>)}
            </select>
          )}
          <button onClick={() => { setProvData({}); setMigData(null); view === "migration" ? fetchMigration() : fetchProvider(view); }}
            style={{ padding: "4px 8px", background: "#1e293b", border: "1px solid #334155", borderRadius: 6, cursor: "pointer", color: "#94a3b8", display: "flex", alignItems: "center" }}>
            <RefreshCw size={12} />
          </button>
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 10, color: "#64748b" }}>
        {view === "migration" ? (
          <>
            {Object.entries(STRATEGY).map(([k, v]) => (
              <span key={k} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 20, height: 2, background: v.color, display: "inline-block", borderRadius: 1 }} />
                {v.label}
              </span>
            ))}
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ width: 20, height: 1, background: "#64748b", display: "inline-block" }} />
              RELATED_TO
            </span>
          </>
        ) : (
          <>
            {Object.entries(REL).filter(([k]) => k !== "MIGRATES_TO").map(([k, v]) => (
              <span key={k} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 20, height: 1.5, background: v.color, display: "inline-block" }} />
                {k}
              </span>
            ))}
            <span style={{ marginLeft: 8 }}>Cliquer nœud/lien pour détails</span>
          </>
        )}
      </div>

      {/* Graph */}
      {isLoading ? (
        <div style={{ padding: 40, textAlign: "center", color: "#64748b", fontSize: 12 }}>
          <RefreshCw size={18} style={{ animation: "spin 1s linear infinite", marginBottom: 8 }} />
          <div>Chargement…</div>
        </div>
      ) : error ? (
        <div style={{ padding: 16, background: "#fef2f2", border: "1px solid #fca5a5", borderRadius: 8, color: "#991b1b", fontSize: 12 }}>
          Erreur : {error} —{" "}
          <button onClick={() => view === "migration" ? fetchMigration() : fetchProvider(view)}
            style={{ color: "#ef4444", background: "none", border: "none", cursor: "pointer", textDecoration: "underline", fontSize: 12 }}>
            Réessayer
          </button>
        </div>
      ) : view === "migration" && migData ? (
        <div style={{ position: "relative" }}>
          <MigrationGraphCanvas
            data={migData}
            selectedNode={selectedNode} selectedEdge={selectedEdge}
            onNodeClick={(n) => { setSN(prev => prev?.id === n.id ? null : n); setSE(null); }}
            onEdgeClick={(e) => { setSE(prev => prev?.source === e.source && prev?.target === e.target ? null : e); setSN(null); }}
          />
          <NodePanel node={selectedNode} onClose={() => setSN(null)} />
          <EdgePanel edge={selectedEdge} onClose={() => setSE(null)} />
        </div>
      ) : currentData ? (
        <div style={{ position: "relative" }}>
          <ProviderGraphCanvas
            nodes={currentData.nodes || []} edges={currentData.edges || []}
            selectedNode={selectedNode} selectedEdge={selectedEdge}
            onNodeClick={(n) => { setSN(prev => prev?.id === n.id ? null : n); setSE(null); }}
            onEdgeClick={(e) => { setSE(prev => prev?.source === e.source && prev?.target === e.target ? null : e); setSN(null); }}
          />
          <NodePanel node={selectedNode} onClose={() => setSN(null)} />
          <EdgePanel edge={selectedEdge} onClose={() => setSE(null)} />
        </div>
      ) : (
        <div style={{ padding: 40, textAlign: "center", color: "#475569", fontSize: 12 }}>
          Sélectionnez une vue pour afficher le graphe.
        </div>
      )}

      {/* Instructions + Neo4j browser link */}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 11, color: "#475569" }}>
        <span>🖱 <strong style={{ color: "#64748b" }}>Clic</strong> nœud/lien → détails</span>
        <span>🖱 <strong style={{ color: "#64748b" }}>Scroll</strong> → zoom</span>
        <span>🖱 <strong style={{ color: "#64748b" }}>Drag</strong> → déplacer</span>
        <a href="http://localhost:7474/browser/" target="_blank" rel="noreferrer"
          style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4, color: "#60a5fa", textDecoration: "none", fontSize: 11 }}>
          <Database size={11} /> Neo4j Browser <ExternalLink size={10} />
        </a>
      </div>

      {/* Neo4j Cypher queries reference */}
      <details style={{ background: "#1e293b", borderRadius: 8, border: "1px solid #334155" }}>
        <summary style={{ padding: "8px 14px", cursor: "pointer", fontSize: 11, fontWeight: 600, color: "#94a3b8", userSelect: "none" }}>
          Requêtes Cypher utiles pour Neo4j Browser
        </summary>
        <div style={{ padding: "0 14px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
          {[
            {
              label: "Top 50 ressources AWS les plus connectées",
              q: "MATCH (n:Resource {provider: 'aws'})-[r]-() WITH n, count(r) as deg ORDER BY deg DESC LIMIT 50 RETURN n, deg",
            },
            {
              label: "Graphe global Azure (top 60)",
              q: "MATCH (n:Resource {provider: 'azurerm'})-[r]-() WITH n, count(r) as deg ORDER BY deg DESC LIMIT 60 MATCH (n)-[rel:RELATED_TO|REFERENCES]-(m:Resource) RETURN n, rel, m LIMIT 200",
            },
            {
              label: "Sous-graphe migration (ressources source + cible)",
              q: `MATCH (src:Resource) WHERE src.name IN ['aws_db_instance','aws_s3_bucket','aws_iam_role']\nMATCH (tgt:Resource) WHERE tgt.name IN ['azurerm_postgresql_flexible_server','azurerm_storage_account','azurerm_user_assigned_identity']\nRETURN src, tgt`,
            },
            {
              label: "Voisins directs d'une ressource (2 niveaux)",
              q: "MATCH p=(n:Resource {name: 'aws_db_instance'})-[r:RELATED_TO|REFERENCES*1..2]-(m:Resource) RETURN p LIMIT 50",
            },
            {
              label: "Communautés Louvain",
              q: "MATCH (n:Resource)-[:IN_COMMUNITY]->(c:Community) RETURN c.id as community, collect(n.name)[0..8] as resources, count(n) as size ORDER BY size DESC LIMIT 20",
            },
          ].map(({ label, q }) => (
            <div key={label}>
              <div style={{ fontSize: 10, color: "#64748b", marginBottom: 3 }}>{label}</div>
              <code style={{
                display: "block", padding: "6px 10px", background: "#0f172a",
                borderRadius: 6, fontSize: 10, color: "#a78bfa", fontFamily: "monospace",
                border: "1px solid #1e293b", whiteSpace: "pre-wrap", wordBreak: "break-all",
              }}>{q}</code>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
