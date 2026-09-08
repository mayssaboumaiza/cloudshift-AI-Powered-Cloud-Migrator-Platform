import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  Database, GitBranch, Link, Layers, ChevronDown, ChevronUp,
  Info, Network, Eye, Users, ArrowRight, Code2, Search, ZoomIn, ZoomOut, Maximize2,
} from "lucide-react";

// ── Palette ───────────────────────────────────────────────────────────────────
const COMMUNITY_PALETTE = [
  "#60a5fa","#fbbf24","#34d399","#f87171","#a78bfa",
  "#22d3ee","#fb923c","#a3e635","#f472b6","#818cf8",
  "#2dd4bf","#fb7185","#c084fc","#38bdf8","#facc15",
  "#94a3b8","#0ea5e9","#e879f9","#4ade80","#ff6b6b",
];
const communityColor = (id) => {
  if (id == null) return "#94a3b8";
  const raw = String(id);
  const num = parseInt(raw.includes("_") ? raw.split("_").pop() : raw, 10);
  return COMMUNITY_PALETTE[(isNaN(num) ? 0 : num) % COMMUNITY_PALETTE.length];
};

const PROVIDER_COLORS = {
  aws:     { bg: "rgba(251,146,60,0.10)", border: "#fb923c", text: "#c2410c", dot: "#fb923c" },
  google:  { bg: "rgba(96,165,250,0.10)", border: "#60a5fa", text: "#1d4ed8", dot: "#60a5fa" },
  gcp:     { bg: "rgba(96,165,250,0.10)", border: "#60a5fa", text: "#1d4ed8", dot: "#60a5fa" },
  azurerm: { bg: "rgba(52,211,153,0.10)", border: "#34d399", text: "#065f46", dot: "#34d399" },
  azure:   { bg: "rgba(52,211,153,0.10)", border: "#34d399", text: "#065f46", dot: "#34d399" },
};
const providerStyle = (name = "") => {
  const key = Object.keys(PROVIDER_COLORS).find((k) => name.toLowerCase().startsWith(k));
  return PROVIDER_COLORS[key] || { bg: "rgba(148,163,184,0.08)", border: "#cbd5e1", text: "#64748b", dot: "#94a3b8" };
};

// ── Badges ────────────────────────────────────────────────────────────────────
function ResourceBadge({ name, role = "primary" }) {
  const s = providerStyle(name);
  const roleLabel = role === "companion" ? "hop-1" : role === "hop2" ? "hop-2" : role === "related" ? "related" : null;
  const roleBg    = role === "companion" ? "rgba(251,191,36,0.15)" : role === "hop2" ? "rgba(167,139,250,0.15)" : "rgba(148,163,184,0.1)";
  const roleText  = role === "companion" ? "#b45309" : role === "hop2" ? "#7c3aed" : "#64748b";
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "3px 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
      background: s.bg, border: `1px solid ${s.border}`, color: s.text,
      fontFamily: "monospace",
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: s.dot, flexShrink: 0 }} />
      {name}
      {roleLabel && (
        <span style={{ marginLeft: 3, padding: "1px 6px", borderRadius: 10, background: roleBg, color: roleText, fontSize: 9, fontWeight: 700 }}>
          {roleLabel}
        </span>
      )}
    </span>
  );
}

// ── Mini SVG sub-graph (keeps dark canvas — it's a visualization) ─────────────
function MiniGraph({ resource, companions, hop2Nodes, related }) {
  const primary  = { name: resource, depth: 0 };
  const hop1     = companions.map((n) => ({ name: n, depth: 1 }));
  const hop2     = (hop2Nodes || []).slice(0, 4).map((n) => ({ name: n.resource, depth: 2, via: n.via }));
  const rel      = related.slice(0, 2).map((n) => ({ name: n, depth: 1, rel: true }));
  const allNodes = [primary, ...hop1, ...hop2, ...rel];
  const cx = 200, cy = 110;

  const positions = allNodes.map((node, i) => {
    if (i === 0) return { x: cx, y: cy };
    if (node.depth === 1) {
      const h1 = allNodes.filter((n, j) => j > 0 && n.depth === 1);
      const idx = h1.findIndex((n) => n.name === node.name);
      const angle = ((2 * Math.PI) / h1.length) * idx - Math.PI / 2;
      return { x: cx + 72 * Math.cos(angle), y: cy + 72 * Math.sin(angle) };
    }
    const h2  = allNodes.filter((n, j) => j > 0 && n.depth === 2);
    const idx = h2.findIndex((n) => n.name === node.name);
    const angle = ((2 * Math.PI) / Math.max(h2.length, 1)) * idx - Math.PI / 2;
    return { x: cx + 128 * Math.cos(angle), y: cy + 128 * Math.sin(angle) };
  });

  const nodeColor = (node, i) =>
    i === 0 ? "#60a5fa" : node.rel ? "#94a3b8" : node.depth === 1 ? "#fbbf24" : "#a78bfa";
  const edgeColor = (node) => node.rel ? "#475569" : node.depth === 1 ? "#fbbf24" : "#a78bfa";
  const edgeDash  = (node) => node.rel ? "5 4" : node.depth === 2 ? "3 3" : "0";
  const label     = (name) => name.length > 22 ? name.slice(0, 20) + "…" : name;

  return (
    <svg width="400" height="230" style={{ overflow: "visible", borderRadius: 10 }}>
      <defs>
        <filter id="miniGlow">
          <feGaussianBlur stdDeviation="2.5" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      <rect width="400" height="230" rx="10" fill="#0f172a" />
      {allNodes.slice(1).map((node, i) => {
        let srcPos = positions[0];
        if (node.depth === 2 && node.via) {
          const viaIdx = allNodes.findIndex((n) => n.name === node.via);
          if (viaIdx > 0) srcPos = positions[viaIdx];
        }
        const dst = positions[i + 1];
        return (
          <line key={i}
            x1={srcPos.x} y1={srcPos.y} x2={dst.x} y2={dst.y}
            stroke={edgeColor(node)} strokeWidth={1.5}
            strokeDasharray={edgeDash(node)} opacity={0.6}
          />
        );
      })}
      {allNodes.map((node, i) => {
        const { x, y } = positions[i];
        const r = i === 0 ? 11 : node.depth === 2 ? 5 : 7;
        const color = nodeColor(node, i);
        return (
          <g key={i} transform={`translate(${x},${y})`} filter="url(#miniGlow)">
            <circle r={r} fill={color} opacity={0.9} />
            <text y={r + 13} textAnchor="middle" fontSize={9} fill="#cbd5e1" fontFamily="monospace">
              {label(node.name)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ── Force-directed graph (dark SVG canvas — intentional) ─────────────────────
function ForceGraph({ visData, width = 720, height = 520 }) {
  const svgRef   = useRef(null);
  const [positions, setPositions] = useState({});
  const [tooltip, setTooltip]     = useState(null);
  const [dragging, setDragging]   = useState(null);
  const [colorBy, setColorBy]     = useState("community");
  const [search, setSearch]       = useState("");
  const [zoom, setZoom]           = useState(1);
  const animRef = useRef(null);
  const posRef  = useRef({});
  const velRef  = useRef({});

  const nodes = visData?.nodes || [];
  const edges = visData?.edges || [];

  const degreeMap = useMemo(() => {
    const d = {};
    nodes.forEach((n) => { d[n.id] = 0; });
    edges.forEach(({ source, target }) => {
      if (d[source] !== undefined) d[source]++;
      if (d[target] !== undefined) d[target]++;
    });
    return d;
  }, [nodes, edges]);

  const nodeSetKey = nodes.map((n) => n.id).join(",");

  useEffect(() => {
    if (!nodes.length) return;
    const cx = width / 2, cy = height / 2;
    const r  = Math.min(width, height) * 0.38;
    const init = {}, vel = {};
    nodes.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / nodes.length;
      init[n.id] = { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
      vel[n.id]  = { vx: 0, vy: 0 };
    });
    posRef.current = init;
    velRef.current = vel;
    setPositions({ ...init });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeSetKey]);

  const tick = useCallback(() => {
    const pos = posRef.current, vel = velRef.current;
    if (!Object.keys(pos).length) return;
    const REPEL = 2800, SPRING_LEN = 90, SPRING_K = 0.055, DAMP = 0.82, GRAVITY = 0.012;
    const cx = width / 2, cy = height / 2;
    const nodeIds = nodes.map((n) => n.id);
    for (let i = 0; i < nodeIds.length; i++) {
      for (let j = i + 1; j < nodeIds.length; j++) {
        const a = pos[nodeIds[i]], b = pos[nodeIds[j]];
        if (!a || !b) continue;
        const dx = a.x - b.x, dy = a.y - b.y;
        const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
        const force = REPEL / (dist * dist);
        vel[nodeIds[i]].vx += (dx / dist) * force; vel[nodeIds[i]].vy += (dy / dist) * force;
        vel[nodeIds[j]].vx -= (dx / dist) * force; vel[nodeIds[j]].vy -= (dy / dist) * force;
      }
    }
    edges.forEach(({ source, target }) => {
      const a = pos[source], b = pos[target];
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const force = SPRING_K * (dist - SPRING_LEN);
      vel[source].vx += (dx / dist) * force; vel[source].vy += (dy / dist) * force;
      vel[target].vx -= (dx / dist) * force; vel[target].vy -= (dy / dist) * force;
    });
    nodeIds.forEach((id) => {
      if (!pos[id]) return;
      vel[id].vx += (cx - pos[id].x) * GRAVITY;
      vel[id].vy += (cy - pos[id].y) * GRAVITY;
      vel[id].vx *= DAMP; vel[id].vy *= DAMP;
      pos[id] = {
        x: Math.max(20, Math.min(width  - 20, pos[id].x + vel[id].vx)),
        y: Math.max(20, Math.min(height - 20, pos[id].y + vel[id].vy)),
      };
    });
    setPositions({ ...pos });
    animRef.current = requestAnimationFrame(tick);
  }, [nodes, edges, width, height]);

  useEffect(() => {
    animRef.current = requestAnimationFrame(tick);
    const stop = setTimeout(() => cancelAnimationFrame(animRef.current), 5000);
    return () => { cancelAnimationFrame(animRef.current); clearTimeout(stop); };
  }, [tick]);

  const edgeColor = (rel) =>
    rel === "COMPANION" ? "#fbbf24" : rel === "DEPENDS_ON" ? "#f87171" : "#1e293b";
  const edgeWidth = (rel) =>
    rel === "COMPANION" ? 2 : rel === "DEPENDS_ON" ? 1.8 : 1;
  const nodeRadius = (n) => {
    const deg = degreeMap[n.id] || 0;
    return Math.max(5, Math.min(14, 5 + deg * 1.2));
  };
  const nodeColor = (n) =>
    colorBy === "community" ? communityColor(n.community_id) : providerStyle(n.id).dot;
  const matchSearch = (n) =>
    !search || n.id.toLowerCase().includes(search.toLowerCase());

  const handleMouseDown = (e, nodeId) => {
    e.stopPropagation();
    setDragging(nodeId);
    cancelAnimationFrame(animRef.current);
  };
  const handleMouseMove = useCallback((e) => {
    if (!dragging || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const x = (e.clientX - rect.left) / zoom;
    const y = (e.clientY - rect.top) / zoom;
    posRef.current[dragging] = { x, y };
    if (velRef.current[dragging]) { velRef.current[dragging].vx = 0; velRef.current[dragging].vy = 0; }
    setPositions((prev) => ({ ...prev, [dragging]: { x, y } }));
  }, [dragging, zoom]);
  const handleMouseUp = () => setDragging(null);

  if (!nodes.length) {
    return (
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        height: 300, background: "var(--surface-1,#f8fafc)", borderRadius: 16,
        color: "var(--text-tertiary,#9ca3af)", border: "1px dashed var(--surface-3,#e2e8f0)",
      }}>
        <Network size={40} style={{ marginBottom: 12, opacity: 0.3 }} />
        <div style={{ fontSize: 14 }}>Knowledge graph non disponible</div>
        <div style={{ fontSize: 12, marginTop: 6, color: "var(--text-disabled,#cbd5e1)" }}>PostgreSQL + pgvector requis</div>
      </div>
    );
  }

  return (
    <div style={{ position: "relative", userSelect: "none" }}>
      {/* Controls — light theme */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: 1, minWidth: 180 }}>
          <Search size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--text-tertiary,#9ca3af)" }} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Rechercher un nœud…"
            style={{
              width: "100%", padding: "6px 10px 6px 30px", borderRadius: 20,
              border: "1px solid var(--surface-3,#e2e8f0)",
              background: "var(--surface-0,#fff)",
              color: "var(--text-primary,#0a0a0b)",
              fontSize: 12, outline: "none",
            }}
          />
        </div>

        <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <span style={{ fontSize: 11, color: "var(--text-tertiary,#9ca3af)" }}>Couleur :</span>
          {["community", "provider"].map((m) => (
            <button key={m} onClick={() => setColorBy(m)} style={{
              padding: "4px 12px", borderRadius: 12, fontSize: 11, cursor: "pointer",
              border: "1px solid var(--surface-3,#e2e8f0)",
              background: colorBy === m ? "linear-gradient(135deg,#6366f1,#3b82f6)" : "var(--surface-1,#f8fafc)",
              color: colorBy === m ? "#fff" : "var(--text-secondary,#6b7280)",
              fontWeight: colorBy === m ? 700 : 400,
              boxShadow: colorBy === m ? "0 0 10px rgba(99,102,241,0.3)" : "none",
            }}>{m === "community" ? "Communauté" : "Provider"}</button>
          ))}
        </div>

        <div style={{ display: "flex", gap: 4 }}>
          {[
            { icon: <ZoomIn size={13} />, fn: () => setZoom((z) => Math.min(z + 0.2, 2)) },
            { icon: <ZoomOut size={13} />, fn: () => setZoom((z) => Math.max(z - 0.2, 0.4)) },
            { icon: <Maximize2 size={13} />, fn: () => setZoom(1) },
          ].map((b, i) => (
            <button key={i} onClick={b.fn} style={lightZoomBtn}>{b.icon}</button>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div style={{ display: "flex", gap: 16, marginBottom: 10, fontSize: 11, color: "var(--text-tertiary,#9ca3af)", flexWrap: "wrap" }}>
        {[
          { color: "#fbbf24", label: "COMPANION", dash: false },
          { color: "#f87171", label: "DEPENDS_ON", dash: false },
          { color: "#94a3b8", label: "RELATED_TO", dash: true },
        ].map((l) => (
          <span key={l.label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <svg width="24" height="10">
              <line x1="0" y1="5" x2="24" y2="5" stroke={l.color} strokeWidth="2" strokeDasharray={l.dash ? "4 3" : "0"} />
            </svg>
            {l.label}
          </span>
        ))}
        <span style={{ marginLeft: "auto" }}>Taille des nœuds = degré de connectivité</span>
      </div>

      {/* SVG canvas — dark is intentional for graph readability */}
      <div style={{ borderRadius: 16, overflow: "hidden", border: "1px solid var(--surface-3,#e2e8f0)", boxShadow: "0 2px 12px rgba(0,0,0,0.06)" }}>
        <svg
          ref={svgRef}
          width={width} height={height}
          style={{ background: "#0f172a", cursor: dragging ? "grabbing" : "grab", display: "block" }}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        >
          <defs>
            <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <filter id="glowStrong" x="-100%" y="-100%" width="300%" height="300%">
              <feGaussianBlur stdDeviation="5" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            {["#fbbf24", "#f87171", "#334155"].map((color, i) => (
              <marker key={i} id={`arrow-${i}`} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
                <path d="M0,0 L0,6 L8,3 z" fill={color} opacity="0.8" />
              </marker>
            ))}
            <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
              <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" strokeWidth="0.5" />
            </pattern>
          </defs>
          <rect width={width} height={height} fill="url(#grid)" />
          <g transform={`scale(${zoom})`} style={{ transformOrigin: "center" }}>
            {edges.map((e, i) => {
              const a = positions[e.source], b = positions[e.target];
              if (!a || !b) return null;
              const markerIdx = e.relation === "COMPANION" ? 0 : e.relation === "DEPENDS_ON" ? 1 : 2;
              const dimmed = search && !matchSearch({ id: e.source }) && !matchSearch({ id: e.target });
              return (
                <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  stroke={edgeColor(e.relation)} strokeWidth={edgeWidth(e.relation)}
                  strokeDasharray={e.relation === "RELATED_TO" ? "5 4" : "0"}
                  opacity={dimmed ? 0.05 : 0.5}
                  markerEnd={`url(#arrow-${markerIdx})`}
                />
              );
            })}
            {nodes.map((n) => {
              const pos = positions[n.id];
              if (!pos) return null;
              const color = nodeColor(n);
              const r = nodeRadius(n);
              const isHovered = tooltip?.id === n.id;
              const isMatched = matchSearch(n);
              const dimmed = search && !isMatched;
              return (
                <g key={n.id} transform={`translate(${pos.x},${pos.y})`} style={{ cursor: "grab" }}
                  onMouseDown={(e) => handleMouseDown(e, n.id)}
                  onMouseEnter={() => setTooltip({ id: n.id, x: pos.x, y: pos.y, desc: n.description, community: n.community_label, degree: degreeMap[n.id] || 0 })}
                  onMouseLeave={() => setTooltip(null)}
                  opacity={dimmed ? 0.12 : 1}
                  filter={isHovered ? "url(#glowStrong)" : isMatched && search ? "url(#glow)" : "none"}
                >
                  {isHovered && <circle r={r + 5} fill="none" stroke={color} strokeWidth="1.5" opacity="0.4" />}
                  <circle r={r + 2} fill={color} opacity="0.15" />
                  <circle r={r} fill={color} opacity={0.92}
                    stroke={isHovered ? "#fff" : "rgba(255,255,255,0.1)"}
                    strokeWidth={isHovered ? 2 : 0.5}
                  />
                  {(isHovered || (search && isMatched)) && (
                    <text y={-(r + 7)} textAnchor="middle" fontSize={10} fill="#e2e8f0"
                      fontFamily="monospace" fontWeight={600} style={{ pointerEvents: "none" }}>
                      {n.label.length > 30 ? n.label.slice(0, 28) + "…" : n.label}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* Floating tooltip */}
      {tooltip && (
        <div style={{
          position: "absolute", pointerEvents: "none",
          left: Math.min(tooltip.x * zoom + 20, width - 220),
          top: Math.max(tooltip.y * zoom - 70, 0),
          background: "var(--surface-0,#fff)",
          backdropFilter: "blur(12px)",
          color: "var(--text-primary,#0a0a0b)",
          padding: "10px 14px", borderRadius: 10, fontSize: 11,
          maxWidth: 220, zIndex: 20, lineHeight: 1.6,
          border: "1px solid var(--surface-3,#e2e8f0)",
          boxShadow: "0 4px 20px rgba(0,0,0,0.12)",
        }}>
          <div style={{ fontWeight: 700, fontFamily: "monospace", marginBottom: 4, color: "#6366f1" }}>{tooltip.id}</div>
          {tooltip.community && (
            <div style={{ color: "var(--text-tertiary,#9ca3af)", fontSize: 10, marginBottom: 3 }}>
              Communauté : {tooltip.community}
            </div>
          )}
          <div style={{ color: "var(--text-tertiary,#9ca3af)", fontSize: 10, marginBottom: 4 }}>
            Degré : {tooltip.degree} connexion{tooltip.degree !== 1 ? "s" : ""}
          </div>
          {tooltip.desc && (
            <div style={{ color: "var(--text-secondary,#6b7280)", fontSize: 10, borderTop: "1px solid var(--surface-3,#e2e8f0)", paddingTop: 5 }}>
              {tooltip.desc.slice(0, 120)}{tooltip.desc.length > 120 ? "…" : ""}
            </div>
          )}
        </div>
      )}

      {/* Footer */}
      <div style={{ marginTop: 10, display: "flex", gap: 16, fontSize: 11, color: "var(--text-tertiary,#9ca3af)" }}>
        <span>{nodes.length} nœuds</span>
        <span>{edges.length} arêtes</span>
        <span>colorés par {colorBy === "community" ? "communauté Louvain" : "provider"}</span>
        {zoom !== 1 && <span>zoom ×{zoom.toFixed(1)}</span>}
      </div>
    </div>
  );
}

const lightZoomBtn = {
  width: 28, height: 28, display: "flex", alignItems: "center", justifyContent: "center",
  background: "var(--surface-1,#f8fafc)", border: "1px solid var(--surface-3,#e2e8f0)",
  borderRadius: 8, cursor: "pointer", color: "var(--text-secondary,#6b7280)",
};

// ── Communities tab ───────────────────────────────────────────────────────────
function CommunitiesTab({ provider }) {
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/v1/graph-rag/communities${provider ? `?provider=${provider}` : ""}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [provider]);

  if (loading) return (
    <div style={{ padding: 32, color: "var(--text-tertiary,#9ca3af)", fontSize: 13, textAlign: "center" }}>
      Chargement des communautés…
    </div>
  );
  if (!data?.available) return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--text-tertiary,#9ca3af)" }}>
      <Users size={32} style={{ opacity: 0.3, marginBottom: 10 }} />
      <div style={{ fontSize: 14 }}>Communautés non disponibles</div>
      <div style={{ fontSize: 12, marginTop: 6 }}>
        Lancer <code style={{ background: "var(--surface-2,#f1f5f9)", padding: "2px 6px", borderRadius: 4 }}>RAGBuilder.build_all()</code>
      </div>
    </div>
  );

  const communities = data.communities || [];
  return (
    <div>
      <div style={{ marginBottom: 18, fontSize: 12, color: "var(--text-secondary,#6b7280)", lineHeight: 1.6 }}>
        <strong style={{ color: "var(--text-primary,#0a0a0b)" }}>{communities.length}</strong> communautés détectées par l'algorithme{" "}
        <span style={{ color: "#6366f1", fontWeight: 600 }}>Louvain</span> (NetworkX).
        Chaque communauté regroupe des ressources Terraform fortement interconnectées.
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
        {communities.map((c) => {
          const color = communityColor(c.id);
          return (
            <div key={c.id} style={{
              background: "var(--surface-0,#fff)",
              border: `1px solid ${color}30`,
              borderLeft: `3px solid ${color}`,
              borderRadius: 12, padding: "14px 16px",
              boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
              transition: "box-shadow 0.2s",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: color, flexShrink: 0 }} />
                <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary,#0a0a0b)" }}>{c.label}</span>
                <span style={{ marginLeft: "auto", fontSize: 11, color,
                  background: `${color}15`, padding: "2px 8px", borderRadius: 10, fontWeight: 600 }}>
                  {c.size} res.
                </span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {(c.members_sample || []).map((m) => (
                  <span key={m} style={{
                    fontSize: 10, padding: "2px 7px", borderRadius: 10,
                    background: "var(--surface-1,#f8fafc)",
                    border: "1px solid var(--surface-3,#e2e8f0)",
                    color: "var(--text-secondary,#6b7280)",
                    fontFamily: "monospace",
                  }}>{m}</span>
                ))}
                {c.size > (c.members_sample?.length || 0) && (
                  <span style={{ fontSize: 10, color: "var(--text-tertiary,#9ca3af)" }}>
                    +{c.size - (c.members_sample?.length || 0)} autres
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Resource card (sub-graph) ─────────────────────────────────────────────────
function ResourceCard({ resource, traversal }) {
  const [expanded, setExpanded] = useState(false);
  const companions = traversal?.companions_fetched || [];
  const related    = traversal?.related_resources  || [];
  const multiHop   = traversal?.multi_hop_nodes    || [];
  const hop2Nodes  = multiHop.filter((n) => n.depth === 2);
  const hop2Count  = traversal?.hop2_count ?? hop2Nodes.length;
  const s          = providerStyle(resource);

  return (
    <div style={{
      background: "var(--surface-0,#fff)",
      border: "1px solid var(--surface-3,#e2e8f0)",
      borderRadius: 12, marginBottom: 10, overflow: "hidden",
      transition: "border-color 0.2s, box-shadow 0.2s",
      boxShadow: expanded ? "0 2px 12px rgba(0,0,0,0.06)" : "none",
      borderColor: expanded ? `${s.border}60` : "var(--surface-3,#e2e8f0)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "12px 16px", cursor: "pointer",
        background: expanded ? "var(--surface-1,#f8fafc)" : "transparent",
      }}
        onClick={() => setExpanded((v) => !v)}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: s.dot, flexShrink: 0 }} />
        <span style={{ fontFamily: "monospace", fontSize: 12, color: "var(--text-primary,#0a0a0b)", fontWeight: 600 }}>{resource}</span>
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap" }}>
          {companions.length > 0 && (
            <span style={{ fontSize: 10, color: "#b45309", background: "rgba(251,191,36,0.12)", border: "1px solid rgba(251,191,36,0.3)", padding: "1px 8px", borderRadius: 10 }}>
              +{companions.length} hop-1
            </span>
          )}
          {hop2Count > 0 && (
            <span style={{ fontSize: 10, color: "#7c3aed", background: "rgba(167,139,250,0.12)", border: "1px solid rgba(167,139,250,0.3)", padding: "1px 8px", borderRadius: 10, display: "flex", alignItems: "center", gap: 3 }}>
              <ArrowRight size={9} />{hop2Count} hop-2
            </span>
          )}
          {related.length > 0 && (
            <span style={{ fontSize: 10, color: "var(--text-tertiary,#9ca3af)", background: "var(--surface-2,#f1f5f9)", border: "1px solid var(--surface-3,#e2e8f0)", padding: "1px 8px", borderRadius: 10 }}>
              {related.length} liés
            </span>
          )}
        </div>
        <span style={{ marginLeft: "auto", color: "var(--text-tertiary,#9ca3af)" }}>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </div>

      {expanded && (
        <div style={{ padding: "14px 16px", borderTop: "1px solid var(--surface-3,#e2e8f0)" }}>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 200 }}>
              {companions.length > 0 && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "#b45309", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    Hop 1 — Companions directs
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {companions.map((c) => <ResourceBadge key={c} name={c} role="companion" />)}
                  </div>
                </div>
              )}
              {hop2Nodes.length > 0 && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "#7c3aed", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    Hop 2 — Voisins indirects
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {hop2Nodes.map((n) => (
                      <span key={n.resource} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                        <ResourceBadge name={n.resource} role="hop2" />
                        <span style={{ fontSize: 9, color: "var(--text-tertiary,#9ca3af)" }}>via {n.via}</span>
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {related.length > 0 && (
                <div>
                  <div style={{ fontSize: 10, fontWeight: 700, color: "var(--text-tertiary,#9ca3af)", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>Ressources liées</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                    {related.map((r) => <ResourceBadge key={r} name={r} role="related" />)}
                  </div>
                </div>
              )}
              {companions.length === 0 && hop2Nodes.length === 0 && related.length === 0 && (
                <span style={{ fontSize: 12, color: "var(--text-tertiary,#9ca3af)" }}>Aucune ressource liée dans le graphe.</span>
              )}
            </div>
            {(companions.length > 0 || hop2Nodes.length > 0) && (
              <div style={{ flexShrink: 0 }}>
                <MiniGraph resource={resource} companions={companions} hop2Nodes={hop2Nodes} related={related} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Argument nodes tab ────────────────────────────────────────────────────────
function ArgumentNodesTab({ resourceList, argumentNodeCount }) {
  const [selected, setSelected] = useState(resourceList[0] || "");
  const [data, setData]         = useState(null);
  const [loading, setLoading]   = useState(false);

  useEffect(() => {
    if (!selected && resourceList.length > 0) setSelected(resourceList[0]);
  }, [resourceList, selected]);

  const loadArgs = (resource) => {
    if (!resource) return;
    setSelected(resource);
    setLoading(true);
    fetch(`/api/v1/graph-rag/context/${encodeURIComponent(resource)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((d) => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  };

  if (!argumentNodeCount || argumentNodeCount === "—") return (
    <div style={{
      padding: "40px 24px", textAlign: "center",
      border: "1px dashed var(--surface-3,#e2e8f0)", borderRadius: 16,
      color: "var(--text-tertiary,#9ca3af)",
    }}>
      <Code2 size={32} style={{ marginBottom: 12, opacity: 0.3 }} />
      <div style={{ fontSize: 14, marginBottom: 8 }}>Nœuds d'arguments non disponibles</div>
      <div style={{ fontSize: 12 }}>
        Lancer <code style={{ background: "var(--surface-2,#f1f5f9)", padding: "2px 6px", borderRadius: 4, color: "#6366f1" }}>RAGBuilder.build_all(force=True)</code>
      </div>
    </div>
  );

  const companions = data?.traversal?.companions_fetched || [];

  return (
    <div>
      <div style={{
        padding: "12px 16px", background: "rgba(99,102,241,0.06)",
        border: "1px solid rgba(99,102,241,0.2)", borderRadius: 10,
        fontSize: 12, color: "#6366f1", lineHeight: 1.7, marginBottom: 18,
      }}>
        <strong>Nekrasov et al. 2025 — Graph RAG argument-level.</strong>{" "}
        La table <code style={{ background: "rgba(99,102,241,0.1)", padding: "1px 5px", borderRadius: 4 }}>tf_arguments</code> stocke
        un embedding par argument Terraform. Cela permet à l'Agent 02 de générer du HCL avec les bons types et contraintes.{" "}
        <span style={{ color: "#7c3aed" }}>TV Pass +10% vs RAG naïf.</span>
      </div>

      {resourceList.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 18 }}>
          {resourceList.map((r) => {
            const s = providerStyle(r);
            const active = selected === r;
            return (
              <button key={r} onClick={() => loadArgs(r)} style={{
                padding: "5px 14px", borderRadius: 20, fontSize: 11, cursor: "pointer",
                border: active ? `1px solid ${s.border}` : "1px solid var(--surface-3,#e2e8f0)",
                background: active ? s.bg : "var(--surface-0,#fff)",
                color: active ? s.text : "var(--text-secondary,#6b7280)",
                fontFamily: "monospace", fontWeight: active ? 700 : 400,
                boxShadow: active ? `0 0 10px ${s.dot}20` : "none",
              }}>{r}</button>
            );
          })}
        </div>
      )}

      {loading && (
        <div style={{ padding: 24, color: "var(--text-tertiary,#9ca3af)", fontSize: 13, textAlign: "center" }}>Chargement…</div>
      )}

      {data && !loading && (
        <div>
          {data.context && (
            <div style={{
              background: "#0f172a", border: "1px solid #1e293b", borderRadius: 10,
              padding: "14px 16px", fontSize: 11, fontFamily: "monospace",
              whiteSpace: "pre-wrap", maxHeight: 360, overflowY: "auto",
              lineHeight: 1.7, marginBottom: 16, color: "#94a3b8",
            }}>
              {data.context.slice(0, 1800)}{data.context.length > 1800 ? "\n…(tronqué)" : ""}
            </div>
          )}
          {companions.length > 0 && (
            <div>
              <div style={{ fontSize: 10, fontWeight: 700, color: "#b45309", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Companions (HAS_ARGUMENT edges)
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                {companions.map((c) => <ResourceBadge key={c} name={c} role="companion" />)}
              </div>
            </div>
          )}
        </div>
      )}

      {!data && !loading && selected && (
        <div style={{ textAlign: "center", marginTop: 20 }}>
          <button onClick={() => loadArgs(selected)} style={{
            padding: "8px 20px", borderRadius: 20,
            background: "rgba(99,102,241,0.08)",
            border: "1px solid rgba(99,102,241,0.3)",
            color: "#6366f1", cursor: "pointer", fontSize: 12,
          }}>
            Charger les arguments de {selected}
          </button>
        </div>
      )}
    </div>
  );
}

// ── Stats card — light ────────────────────────────────────────────────────────
function StatCard({ icon, label, value, color, tooltip }) {
  return (
    <div title={tooltip || label} style={{
      background: "var(--surface-0,#fff)",
      border: `1px solid ${color}25`,
      borderRadius: 12, padding: "14px 16px",
      display: "flex", alignItems: "center", gap: 12,
      boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
      transition: "box-shadow 0.2s, border-color 0.2s",
    }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = `${color}55`; e.currentTarget.style.boxShadow = `0 4px 16px ${color}15`; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = `${color}25`; e.currentTarget.style.boxShadow = "0 1px 4px rgba(0,0,0,0.04)"; }}
    >
      <span style={{
        width: 36, height: 36, borderRadius: 10,
        background: `${color}12`, border: `1px solid ${color}25`,
        display: "flex", alignItems: "center", justifyContent: "center",
        color, flexShrink: 0,
      }}>{icon}</span>
      <div>
        <div style={{ fontSize: 20, fontWeight: 800, color: "var(--text-primary,#0a0a0b)", lineHeight: 1.1 }}>{value}</div>
        <div style={{ fontSize: 11, color: "var(--text-tertiary,#9ca3af)", marginTop: 2 }}>{label}</div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function RAGGraphSection({ artifacts, migrationPlan, repoUrl, sourceCloud, targetCloud }) {
  const hasPlan = !!(migrationPlan?.resources?.length);
  const [activeTab, setActiveTab]         = useState("subgraph");

  // Fix: switch to personalized tab as soon as plan resources become available
  // (handles async loading where hasPlan is false at mount)
  useEffect(() => {
    if (hasPlan) setActiveTab("subgraph");
  }, [hasPlan]);
  const [visFilter, setVisFilter]         = useState("");
  const [remoteStats, setRemoteStats]     = useState(null);
  const [statsLoading, setStatsLoading]   = useState(false);
  const [fallbackTraversals, setFallbackTraversals] = useState(null);
  const [fallbackLoading, setFallbackLoading]       = useState(false);
  const [fallbackError, setFallbackError]           = useState(null);
  const [autoLoaded, setAutoLoaded]       = useState(false);

  const planResources = useMemo(() => {
    const resources = migrationPlan?.resources || [];
    const list = [];
    resources.forEach((r) => {
      const strategy = (r?.strategy || r?.strategy_7r || "").toUpperCase();
      if (strategy === "RETAIN" || strategy === "RETIRE") return;
      const target = r?.terraform_resource || r?.target_service || r?.target_equivalent || r?.target || "";
      if (target) list.push(target);
    });
    return Array.from(new Set(list));
  }, [migrationPlan]);

  const traversalData      = artifacts?.rag_graph_traversal || fallbackTraversals;
  const traversalStats     = traversalData?.graph_stats || null;
  const stats              = (traversalStats && Object.keys(traversalStats).length > 0)
    ? traversalStats : (remoteStats || {});
  const resourceTraversals = traversalData?.resource_traversals || {};
  const resourceList       = Object.keys(resourceTraversals);
  const resourceChoices    = resourceList.length > 0 ? resourceList : planResources;
  const hasTraversal       = resourceList.length > 0;
  const hasPlanResources   = planResources.length > 0;
  const ragUnavailable     = stats?.available === false;

  useEffect(() => {
    if (artifacts?.rag_graph_traversal || remoteStats || statsLoading) return;
    let cancelled = false;
    setStatsLoading(true);
    fetch("/api/v1/graph-rag/stats")
      .then((r) => r.ok ? r.json() : { available: false })
      .then((d) => { if (!cancelled) setRemoteStats(d); })
      .catch(() => { if (!cancelled) setRemoteStats({ available: false }); })
      .finally(() => { if (!cancelled) setStatsLoading(false); });
    return () => { cancelled = true; };
  }, [artifacts?.rag_graph_traversal, remoteStats, statsLoading]);

  const loadPlanTraversals = useCallback(async () => {
    if (!planResources.length) return;
    setFallbackLoading(true);
    setFallbackError(null);
    try {
      const results = await Promise.all(
        planResources.map(async (resource) => {
          const resp = await fetch(`/api/v1/graph-rag/context/${encodeURIComponent(resource)}`);
          if (!resp.ok) return [resource, null];
          const data = await resp.json();
          return [resource, data?.traversal || null];
        })
      );
      const traversals = {};
      results.forEach(([resource, traversal]) => { if (traversal) traversals[resource] = traversal; });
      setFallbackTraversals({ graph_stats: remoteStats || {}, resource_traversals: traversals });
      if (!Object.keys(traversals).length) setFallbackError("Aucune ressource GraphRAG trouvée pour ce plan.");
    } catch {
      setFallbackError("Impossible de charger le traversal GraphRAG.");
    } finally {
      setFallbackLoading(false);
    }
  }, [planResources, remoteStats]);

  useEffect(() => {
    if (artifacts?.rag_graph_traversal || autoLoaded) return;
    if (!planResources.length) return;
    setAutoLoaded(true);
    loadPlanTraversals();
  }, [artifacts?.rag_graph_traversal, autoLoaded, planResources, loadPlanTraversals]);

  const TABS = [
    {
      id: "subgraph",
      label: hasPlanResources ? "Ressources Migration" : "Sous-graphes",
      icon: <Eye size={13} />,
      count: resourceChoices.length,
      badge: hasPlanResources ? "personnalisé" : null,
    },
    { id: "communities", label: "Communautés",            icon: <Users size={13} /> },
    { id: "arguments",   label: "Arguments",              icon: <Code2 size={13} />,
      count: stats.argument_node_count > 0 ? stats.argument_node_count : null },
  ];

  return (
    <div>
      {/* Unavailable warning */}
      {ragUnavailable && !statsLoading && (
        <div style={{
          marginBottom: 20, padding: "12px 16px",
          background: "rgba(251,146,60,0.06)", border: "1px solid rgba(251,146,60,0.2)",
          borderRadius: 10, fontSize: 12, color: "#c2410c",
        }}>
          GraphRAG indisponible — PostgreSQL + pgvector requis.
          Lancer <code style={{ background: "rgba(251,146,60,0.1)", padding: "1px 5px", borderRadius: 4 }}>RAGBuilder.build_all()</code> d'abord.
        </div>
      )}

      {/* Migration context header — shown only when this GraphRAG is personalized */}
      {hasPlan && (
        <div style={{
          display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
          padding: "10px 14px", marginBottom: 12,
          background: "linear-gradient(135deg,rgba(99,102,241,0.06),rgba(167,139,250,0.06))",
          border: "1px solid rgba(99,102,241,0.2)", borderRadius: 10,
          fontSize: 12,
        }}>
          <span style={{ fontWeight: 800, fontSize: 13, color: "#7c3aed", display: "flex", alignItems: "center", gap: 5 }}>
            <Eye size={14} /> GraphRAG personnalisé
          </span>
          {repoUrl && (
            <span style={{
              padding: "2px 10px", borderRadius: 12, fontSize: 11, fontWeight: 700,
              background: "rgba(99,102,241,0.1)", color: "#6366f1",
              fontFamily: "monospace",
            }}>
              {repoUrl.replace("https://github.com/", "")}
            </span>
          )}
          {sourceCloud && targetCloud && (
            <span style={{ fontSize: 11, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ fontWeight: 700, color: "#f97316", textTransform: "uppercase" }}>{sourceCloud}</span>
              <ArrowRight size={11} />
              <span style={{ fontWeight: 700, color: "#22c55e", textTransform: "uppercase" }}>{targetCloud}</span>
            </span>
          )}
          <span style={{
            marginLeft: "auto", padding: "2px 10px", borderRadius: 12,
            background: "rgba(34,197,94,0.1)", color: "#16a34a",
            fontSize: 11, fontWeight: 700,
          }}>
            {planResources.length} ressource{planResources.length > 1 ? "s" : ""} ciblée{planResources.length > 1 ? "s" : ""}
          </span>
        </div>
      )}

      {/* Neo4j Browser link */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 14px", marginBottom: 16,
        background: "linear-gradient(135deg,rgba(22,163,74,0.06),rgba(34,211,153,0.06))",
        border: "1px solid rgba(34,211,153,0.25)", borderRadius: 10,
        fontSize: 12,
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

      {/* Stats banner */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 24 }}>
        <StatCard icon={<Database size={18} />}  label="Ressources indexées"  value={stats.resource_count      ?? "—"} color="#60a5fa" />
        <StatCard icon={<GitBranch size={18} />} label="Edges totaux"          value={stats.total_edges         ?? "—"} color="#a78bfa" />
        <StatCard icon={<Link size={18} />}      label="Companions"            value={stats.companion_edges     ?? "—"} color="#fbbf24" />
        <StatCard icon={<Users size={18} />}     label="Communautés Louvain"   value={stats.community_count     ?? "—"} color="#34d399" />
        <StatCard icon={<Code2 size={18} />}     label="Nœuds d'arguments"    value={stats.argument_node_count ?? "—"} color="#818cf8"
          tooltip="Nekrasov 2025 — nœuds argument-level dans tf_arguments" />
        <StatCard icon={<Layers size={18} />}    label="Providers"             value={Object.keys(stats.providers || {}).length} color="#22d3ee" />
      </div>

      {/* Tab nav — light */}
      <div style={{
        display: "flex", gap: 3, marginBottom: 20,
        background: "var(--surface-1,#f8fafc)",
        border: "1px solid var(--surface-3,#e2e8f0)",
        borderRadius: 12, padding: 4, flexWrap: "wrap",
      }}>
        {TABS.map((tab) => {
          const active = activeTab === tab.id;
          return (
            <button key={tab.id} onClick={() => setActiveTab(tab.id)} style={{
              flex: 1, minWidth: 100,
              padding: "8px 14px", borderRadius: 9, fontSize: 12, cursor: "pointer",
              border: "none", display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
              background: active ? "linear-gradient(135deg,#6366f1,#3b82f6)" : "transparent",
              color: active ? "#fff" : "var(--text-secondary,#6b7280)",
              fontWeight: active ? 700 : 400,
              boxShadow: active ? "0 0 15px rgba(99,102,241,0.3)" : "none",
              transition: "all 0.2s",
            }}>
              {tab.icon}
              {tab.label}
              {tab.badge && (
                <span style={{
                  padding: "1px 6px", borderRadius: 10, fontSize: 9, fontWeight: 700,
                  background: active ? "rgba(52,211,153,0.3)" : "rgba(52,211,153,0.15)",
                  color: active ? "#fff" : "#059669",
                }}>{tab.badge}</span>
              )}
              {tab.count != null && (
                <span style={{
                  padding: "1px 7px", borderRadius: 10, fontSize: 10, fontWeight: 700,
                  background: active ? "rgba(255,255,255,0.25)" : "var(--surface-3,#e2e8f0)",
                  color: active ? "#fff" : "var(--text-tertiary,#9ca3af)",
                }}>{tab.count}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div style={{ minHeight: 300 }}>
        {/* Sub-graph */}
        {activeTab === "subgraph" && (
          <>
            {/* Migration resource mapping — source → target */}
            {hasPlanResources && migrationPlan?.resources?.length > 0 && (
              <div style={{ marginBottom: 16, padding: "10px 14px", background: "rgba(99,102,241,0.04)", border: "1px solid rgba(99,102,241,0.15)", borderRadius: 10 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: "#7c3aed", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                  Ressources de cette migration
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {migrationPlan.resources
                    .filter(r => !["RETAIN","RETIRE"].includes((r.strategy_7r || r.strategy || "").toUpperCase()))
                    .map((r, i) => {
                      const src = r.source_service || r.service_name || r.service || "?";
                      const tgt = r.terraform_resource || r.target_service || r.target_equivalent || "?";
                      const strategy = (r.strategy_7r || r.strategy || "").toUpperCase();
                      const stratColor = strategy === "REHOST" ? "#60a5fa" : strategy === "REPLATFORM" ? "#34d399" : strategy === "REFACTOR" ? "#fbbf24" : "#94a3b8";
                      return (
                        <div key={i} style={{
                          display: "flex", alignItems: "center", gap: 5,
                          padding: "4px 10px", borderRadius: 20, fontSize: 10, fontWeight: 600,
                          background: "var(--surface-0,#fff)", border: "1px solid var(--surface-3,#e2e8f0)",
                        }}>
                          <span style={{ color: "#f97316", fontFamily: "monospace" }}>{src}</span>
                          <ArrowRight size={9} color="#94a3b8" />
                          <span style={{ color: "#22c55e", fontFamily: "monospace" }}>{tgt.replace("azurerm_","").replace(/_/g," ")}</span>
                          <span style={{ padding: "1px 5px", borderRadius: 8, background: stratColor + "20", color: stratColor, fontSize: 9 }}>{strategy}</span>
                        </div>
                      );
                    })}
                </div>
              </div>
            )}

            {stats.providers && Object.keys(stats.providers).length > 0 && (
              <div style={{ marginBottom: 18, padding: "10px 14px", background: "var(--surface-1,#f8fafc)", border: "1px solid var(--surface-3,#e2e8f0)", borderRadius: 10, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-tertiary,#9ca3af)" }}>Providers :</span>
                {Object.entries(stats.providers).map(([prov, count]) => {
                  const s = providerStyle(prov);
                  return (
                    <span key={prov} style={{ padding: "3px 12px", borderRadius: 20, fontSize: 11, fontWeight: 600, background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
                      {prov} — {count}
                    </span>
                  );
                })}
              </div>
            )}
            {hasTraversal ? (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
                  <Info size={13} color="var(--text-tertiary,#9ca3af)" />
                  <span style={{ fontSize: 11, color: "var(--text-tertiary,#9ca3af)" }}>
                    Traversal multi-hop via <code style={{ background: "var(--surface-2,#f1f5f9)", padding: "1px 5px", borderRadius: 4, color: "#6366f1" }}>WITH RECURSIVE</code> SQL —
                    <span style={{ color: "#b45309" }}> hop-1</span> = companions directs,
                    <span style={{ color: "#7c3aed" }}> hop-2</span> = voisins indirects
                  </span>
                </div>
                {resourceList.map((resource) => (
                  <ResourceCard key={resource} resource={resource} traversal={resourceTraversals[resource]} />
                ))}
              </>
            ) : (
              <div style={{ textAlign: "center", padding: 40, color: "var(--text-tertiary,#9ca3af)" }}>
                {hasPlanResources ? (
                  <>
                    <div style={{ marginBottom: 16, fontSize: 13 }}>Traversal indisponible pour les ressources du plan.</div>
                    <button onClick={loadPlanTraversals} disabled={fallbackLoading} style={{
                      padding: "8px 20px", borderRadius: 20, cursor: "pointer",
                      background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.3)",
                      color: "#6366f1", fontSize: 12,
                    }}>
                      {fallbackLoading ? "Chargement…" : "Charger le traversal GraphRAG"}
                    </button>
                    {fallbackError && <div style={{ marginTop: 10, color: "#dc2626", fontSize: 12 }}>{fallbackError}</div>}
                  </>
                ) : (
                  <div style={{ fontSize: 13 }}>Aucune ressource de plan à afficher.</div>
                )}
              </div>
            )}
          </>
        )}


        {/* Communities */}
        {activeTab === "communities" && <CommunitiesTab provider={visFilter} />}

        {/* Arguments */}
        {activeTab === "arguments" && (
          <ArgumentNodesTab resourceList={resourceChoices} argumentNodeCount={stats.argument_node_count} />
        )}
      </div>
    </div>
  );
}
