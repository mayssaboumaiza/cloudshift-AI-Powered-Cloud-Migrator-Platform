import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Send, Loader2, Bot, User, AlertCircle, Sparkles, GitBranch, ZoomIn, ZoomOut, Maximize2, X } from "lucide-react";

// ─── Suggestion chips ────────────────────────────────────────────────────────
const SUGGESTIONS = [
  "Quels sont les services migrés et leurs équivalents Azure ?",
  "Montre-moi les dépendances entre les ressources Terraform générées.",
  "Pourquoi cette stratégie 7R a-t-elle été choisie ?",
  "Quels sont les risques de sécurité à surveiller après déploiement ?",
];

// ─── Colors ──────────────────────────────────────────────────────────────────
const PROVIDER_COLOR = {
  aws:     "#fb923c",
  azurerm: "#38bdf8",
  azure:   "#38bdf8",
  google:  "#4ade80",
  gcp:     "#4ade80",
};
const STRATEGY_COLOR = {
  REHOST:     "#22c55e",
  REPLATFORM: "#3b82f6",
  REFACTOR:   "#f59e0b",
  RETIRE:     "#ef4444",
  RETAIN:     "#94a3b8",
  REPURCHASE: "#8b5cf6",
  RELOCATE:   "#06b6d4",
};
const EDGE_COLOR = {
  MIGRATES_TO:  "#f472b6",
  RELATED_TO:   "#64748b",
  REFERENCES:   "#fbbf24",
  HAS_ARGUMENT: "#34d399",
  COMPANION:    "#fb923c",
};

function providerColor(provider = "") {
  const p = provider.toLowerCase();
  return PROVIDER_COLOR[p] || "#a78bfa";
}
function shortLabel(id = "") {
  const parts = id.split("_");
  return parts.length > 2 ? parts.slice(1).join("_") : id;
}

// ─── Mini inline graph (SVG force-layout simulation) ─────────────────────────
function InlineGraph({ graphData }) {
  const svgRef = useRef(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [positions, setPositions] = useState({});

  const { nodes = [], edges = [], type = "dependency" } = graphData || {};

  // Simple layout: migration = left/right, dependency = circular
  const initialPositions = useMemo(() => {
    const pos = {};
    const W = 600, H = 340;
    if (type === "migration") {
      const sources = nodes.filter(n => n.role === "source");
      const targets = nodes.filter(n => n.role === "target");
      const layoutSide = (list, cx) => {
        list.forEach((n, i) => {
          const y = list.length === 1 ? H / 2 : H * 0.15 + (H * 0.7 / Math.max(list.length - 1, 1)) * i;
          pos[n.id] = { x: cx, y };
        });
      };
      layoutSide(sources, W * 0.22);
      layoutSide(targets, W * 0.78);
    } else {
      nodes.forEach((n, i) => {
        const angle = (2 * Math.PI / Math.max(nodes.length, 1)) * i - Math.PI / 2;
        const r = Math.min(W, H) * 0.35;
        pos[n.id] = { x: W / 2 + r * Math.cos(angle), y: H / 2 + r * Math.sin(angle) };
      });
    }
    return pos;
  }, [nodes, edges, type]);

  useEffect(() => { setPositions(initialPositions); }, [initialPositions]);

  // Drag logic
  const onMouseDown = useCallback((e, nodeId) => {
    e.stopPropagation();
    setDragging({ id: nodeId, startX: e.clientX, startY: e.clientY, origX: positions[nodeId]?.x, origY: positions[nodeId]?.y });
  }, [positions]);

  const onMouseMove = useCallback((e) => {
    if (!dragging) return;
    const dx = (e.clientX - dragging.startX) / zoom;
    const dy = (e.clientY - dragging.startY) / zoom;
    setPositions(prev => ({ ...prev, [dragging.id]: { x: dragging.origX + dx, y: dragging.origY + dy } }));
  }, [dragging, zoom]);

  const onMouseUp = useCallback(() => setDragging(null), []);

  const W = 600, H = 340;
  const NODE_R = 22;

  if (!nodes.length) return null;

  const graphEl = (
    <div style={{
      position: "relative",
      background: "#0f172a",
      borderRadius: fullscreen ? 0 : 10,
      border: fullscreen ? "none" : "1px solid #1e293b",
      overflow: "hidden",
      height: fullscreen ? "100vh" : 420,
      width: "100%",
    }}>
      {/* Toolbar */}
      <div style={{
        position: "absolute", top: 8, right: 8, zIndex: 10,
        display: "flex", gap: 4,
      }}>
        <button onClick={() => setZoom(z => Math.min(z + 0.2, 3))}
          style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 6, padding: "4px 8px", cursor: "pointer", color: "#94a3b8" }}>
          <ZoomIn size={12} />
        </button>
        <button onClick={() => setZoom(z => Math.max(z - 0.2, 0.3))}
          style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 6, padding: "4px 8px", cursor: "pointer", color: "#94a3b8" }}>
          <ZoomOut size={12} />
        </button>
        <button onClick={() => setFullscreen(f => !f)}
          style={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 6, padding: "4px 8px", cursor: "pointer", color: "#94a3b8" }}>
          {fullscreen ? <X size={12} /> : <Maximize2 size={12} />}
        </button>
      </div>

      {/* Type badge */}
      <div style={{
        position: "absolute", top: 8, left: 8, zIndex: 10,
        background: "rgba(167,139,250,0.15)", border: "1px solid rgba(167,139,250,0.3)",
        borderRadius: 8, padding: "3px 8px", fontSize: 10, color: "#a78bfa", fontWeight: 700,
        display: "flex", alignItems: "center", gap: 4,
      }}>
        <GitBranch size={10} />
        {type === "migration" ? "Graphe de migration" : "Graphe de dépendances"}
      </div>

      <svg
        ref={svgRef}
        width="100%" height="100%"
        viewBox={`0 0 ${W} ${H}`}
        style={{ cursor: dragging ? "grabbing" : "default" }}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <defs>
          <marker id="arrow-chat" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#475569" />
          </marker>
          {Object.entries(EDGE_COLOR).map(([k, c]) => (
            <marker key={k} id={`arrow-${k}`} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
              <path d="M0,0 L0,6 L8,3 z" fill={c} />
            </marker>
          ))}
        </defs>

        {/* Edges */}
        {edges.map((e, i) => {
          const sp = positions[e.source];
          const tp = positions[e.target];
          if (!sp || !tp) return null;
          const dx = tp.x - sp.x, dy = tp.y - sp.y;
          const len = Math.sqrt(dx * dx + dy * dy) || 1;
          const ex = tp.x - (dx / len) * NODE_R;
          const ey = tp.y - (dy / len) * NODE_R;
          const color = EDGE_COLOR[e.type] || "#475569";
          const isMigrates = e.type === "MIGRATES_TO";
          const mx = (sp.x + tp.x) / 2, my = (sp.y + tp.y) / 2;
          return (
            <g key={i}>
              <line
                x1={sp.x} y1={sp.y} x2={ex} y2={ey}
                stroke={color}
                strokeWidth={isMigrates ? 2 : 1}
                strokeDasharray={isMigrates ? "none" : "4 3"}
                markerEnd={`url(#arrow-${e.type || "chat"})`}
                opacity={0.8}
              />
              {isMigrates && e.strategy && (
                <text x={mx} y={my - 5} textAnchor="middle" fontSize={8} fill={STRATEGY_COLOR[e.strategy] || "#94a3b8"} fontWeight="700">
                  {e.strategy}
                </text>
              )}
              {!isMigrates && (
                <text x={mx} y={my - 4} textAnchor="middle" fontSize={7} fill={color} opacity={0.7}>
                  {e.type}
                </text>
              )}
            </g>
          );
        })}

        {/* Nodes */}
        {nodes.map((n) => {
          const p = positions[n.id];
          if (!p) return null;
          const color = providerColor(n.provider);
          const isSelected = selectedNode?.id === n.id;
          const label = shortLabel(n.id);
          return (
            <g key={n.id}
              style={{ cursor: "grab" }}
              onMouseDown={(e) => onMouseDown(e, n.id)}
              onClick={() => setSelectedNode(isSelected ? null : n)}
            >
              <circle cx={p.x} cy={p.y} r={NODE_R + (isSelected ? 3 : 0)}
                fill={isSelected ? color : "#1e293b"}
                stroke={color}
                strokeWidth={isSelected ? 3 : 1.5}
                style={{ filter: isSelected ? `drop-shadow(0 0 6px ${color})` : "none" }}
              />
              <text x={p.x} y={p.y + 1} textAnchor="middle" dominantBaseline="middle"
                fontSize={7} fill={isSelected ? "#0f172a" : color} fontWeight="700"
                style={{ pointerEvents: "none", userSelect: "none" }}
              >
                {label.length > 14 ? label.slice(0, 13) + "…" : label}
              </text>
              {n.role === "source" && (
                <circle cx={p.x + NODE_R - 4} cy={p.y - NODE_R + 4} r={5} fill="#fb923c" />
              )}
              {n.role === "target" && (
                <circle cx={p.x + NODE_R - 4} cy={p.y - NODE_R + 4} r={5} fill="#38bdf8" />
              )}
            </g>
          );
        })}
      </svg>

      {/* Node detail panel */}
      {selectedNode && (
        <div style={{
          position: "absolute", bottom: 8, left: 8, right: 8, zIndex: 10,
          background: "#1e293b", borderRadius: 8,
          border: `1px solid ${providerColor(selectedNode.provider)}44`,
          padding: "10px 12px",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <div style={{ width: 8, height: 8, borderRadius: "50%", background: providerColor(selectedNode.provider), flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, fontFamily: "monospace", color: providerColor(selectedNode.provider), fontWeight: 700 }}>
              {selectedNode.id}
            </div>
            {selectedNode.strategy && (
              <div style={{ fontSize: 10, color: STRATEGY_COLOR[selectedNode.strategy] || "#94a3b8", marginTop: 2 }}>
                Stratégie : {selectedNode.strategy}
              </div>
            )}
            <div style={{ fontSize: 10, color: "#475569", marginTop: 1 }}>
              Provider : {selectedNode.provider || "—"}
            </div>
          </div>
          <button onClick={() => setSelectedNode(null)}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#475569" }}>
            <X size={12} />
          </button>
        </div>
      )}
    </div>
  );

  if (fullscreen) {
    return (
      <div style={{ position: "fixed", inset: 0, zIndex: 9999 }}>
        {graphEl}
      </div>
    );
  }
  return graphEl;
}

// ─── Markdown renderer ────────────────────────────────────────────────────────
const mdComponents = {
  p:      ({ children }) => <p style={{ margin: "4px 0", lineHeight: 1.6 }}>{children}</p>,
  strong: ({ children }) => <strong style={{ color: "inherit", fontWeight: 700 }}>{children}</strong>,
  em:     ({ children }) => <em style={{ color: "inherit" }}>{children}</em>,
  ul:     ({ children }) => <ul style={{ margin: "4px 0 4px 16px", padding: 0 }}>{children}</ul>,
  ol:     ({ children }) => <ol style={{ margin: "4px 0 4px 16px", padding: 0 }}>{children}</ol>,
  li:     ({ children }) => <li style={{ margin: "2px 0", lineHeight: 1.5 }}>{children}</li>,
  code:   ({ inline, children }) => inline
    ? <code style={{ background: "rgba(0,0,0,0.25)", padding: "1px 5px", borderRadius: 4, fontSize: "0.85em", fontFamily: "monospace" }}>{children}</code>
    : <pre style={{ background: "rgba(0,0,0,0.3)", padding: "8px 10px", borderRadius: 6, overflowX: "auto", fontSize: "0.82em", margin: "6px 0" }}><code>{children}</code></pre>,
  table:  ({ children }) => <table style={{ borderCollapse: "collapse", fontSize: 12, margin: "6px 0", width: "100%" }}>{children}</table>,
  th:     ({ children }) => <th style={{ borderBottom: "1px solid rgba(255,255,255,0.15)", padding: "4px 8px", textAlign: "left", fontWeight: 700, opacity: 0.8 }}>{children}</th>,
  td:     ({ children }) => <td style={{ borderBottom: "1px solid rgba(255,255,255,0.08)", padding: "3px 8px" }}>{children}</td>,
  h1:     ({ children }) => <h1 style={{ fontSize: 15, margin: "8px 0 4px", fontWeight: 700 }}>{children}</h1>,
  h2:     ({ children }) => <h2 style={{ fontSize: 13, margin: "6px 0 3px", fontWeight: 700 }}>{children}</h2>,
  h3:     ({ children }) => <h3 style={{ fontSize: 12, margin: "5px 0 2px", fontWeight: 700 }}>{children}</h3>,
  blockquote: ({ children }) => <blockquote style={{ borderLeft: "3px solid #a78bfa", margin: "6px 0", paddingLeft: 10, opacity: 0.85 }}>{children}</blockquote>,
};

// ─── Typing indicator ─────────────────────────────────────────────────────────
function TypingIndicator() {
  return (
    <div style={{ display: "flex", gap: 4, alignItems: "center", padding: "4px 0" }}>
      {[0, 1, 2].map(i => (
        <span key={i} style={{
          width: 6, height: 6, borderRadius: "50%", background: "#94a3b8",
          animation: `bounce 1.2s ease-in-out ${i * 0.2}s infinite`,
        }} />
      ))}
      <style>{`@keyframes bounce{0%,80%,100%{transform:translateY(0)}40%{transform:translateY(-6px)}}`}</style>
    </div>
  );
}

// ─── Greeting ─────────────────────────────────────────────────────────────────
const GREETING = {
  role: "assistant",
  content:
    "Bonjour ! Je suis votre assistant **Graph RAG** spécialisé dans cette migration.\n\n" +
    "Posez-moi des questions sur :\n" +
    "- Les ressources migrées et leurs équivalents\n" +
    "- Les **dépendances** et graphes de relations\n" +
    "- Les stratégies 7R et meilleures pratiques cloud\n\n" +
    "💡 Demandez-moi *« Montre-moi les dépendances »* pour voir un graphe interactif.",
};

// ─── Main component ───────────────────────────────────────────────────────────
export default function RAGChatbot({ migrationId, migrationPlan }) {
  const [messages, setMessages]       = useState([GREETING]);
  const [input, setInput]             = useState("");
  const [loading, setLoading]         = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [error, setError]             = useState(null);
  const endRef                        = useRef(null);
  const inputRef                      = useRef(null);

  // Load persistent history
  useEffect(() => {
    if (!migrationId) return;
    fetch(`/api/v1/graph-rag/chat-history/${migrationId}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data?.messages?.length) {
          setMessages([GREETING, ...data.messages.map(m => ({
            role: m.role,
            content: m.content,
            sources: m.sources,
          }))]);
        }
        setHistoryLoaded(true);
      })
      .catch(() => setHistoryLoaded(true));
  }, [migrationId]);

  const persistMessage = useCallback((role, content, sources_count = 0) => {
    if (!migrationId) return;
    fetch(`/api/v1/graph-rag/chat-history/${migrationId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role, content, sources_count }),
    }).catch(() => {});
  }, [migrationId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const resources = migrationPlan?.resources
    ?.map(r => r.target_service || r.target_equivalent)
    .filter(Boolean) || [];

  async function send(text) {
    const q = (text || input).trim();
    if (!q) return;

    setMessages(prev => [...prev, { role: "user", content: q }]);
    persistMessage("user", q);
    setInput("");
    setLoading(true);
    setError(null);

    const history = messages.map(m => ({ role: m.role, content: m.content }));

    try {
      const res = await fetch("/api/v1/graph-rag/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: q,
          migration_id: migrationId || null,
          resources: resources.length ? resources : null,
          history,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `HTTP ${res.status}`);
      }

      const data = await res.json();
      setMessages(prev => [
        ...prev,
        {
          role: "assistant",
          content: data.answer,
          sources: data.sources_count,
          graphData: data.graph_data || null,
        },
      ]);
      persistMessage("assistant", data.answer, data.sources_count ?? 0);
    } catch (e) {
      setError(e.message);
      setMessages(prev => [
        ...prev,
        { role: "assistant", content: `Désolé, une erreur s'est produite : ${e.message}`, isError: true },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  return (
    <div style={{
      display: "flex", flexDirection: "column",
      height: 820, border: "1px solid var(--border,#e4e8f0)",
      borderRadius: 12, background: "var(--card-bg,#fff)",
      overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "12px 16px",
        borderBottom: "1px solid var(--border,#e4e8f0)",
        background: "linear-gradient(135deg,#1e293b 0%,#0f172a 100%)",
        color: "#e2e8f0", flexShrink: 0,
      }}>
        <Sparkles size={16} color="#a78bfa" />
        <span style={{ fontWeight: 700, fontSize: 14 }}>Graph RAG Assistant</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
          {migrationId && (
            <span style={{
              fontSize: 10, padding: "2px 8px", borderRadius: 10,
              background: "rgba(167,139,250,0.15)", color: "#a78bfa", fontWeight: 600,
            }}>
              Contexte actif
            </span>
          )}
          <span style={{
            fontSize: 10, padding: "2px 8px", borderRadius: 10,
            background: "rgba(56,189,248,0.15)", color: "#38bdf8", fontWeight: 600,
          }}>
            Graphes ✦ Markdown
          </span>
        </div>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: "auto", padding: "16px", display: "flex", flexDirection: "column", gap: 12 }}>
        {!historyLoaded && migrationId && (
          <div style={{ textAlign: "center", fontSize: 12, color: "#94a3b8", padding: "8px 0" }}>
            <Loader2 size={13} style={{ animation: "spin 1s linear infinite", marginRight: 6, verticalAlign: "middle" }} />
            Chargement de l'historique…
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} style={{
            display: "flex",
            flexDirection: "column",
            alignItems: msg.role === "user" ? "flex-end" : "flex-start",
            gap: 6,
          }}>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-start", width: "100%", justifyContent: msg.role === "user" ? "flex-end" : "flex-start" }}>
              {msg.role === "assistant" && (
                <div style={{
                  width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                  background: msg.isError ? "#fef2f2" : "linear-gradient(135deg,#a78bfa,#7c3aed)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  {msg.isError ? <AlertCircle size={14} color="#dc2626" /> : <Bot size={14} color="#fff" />}
                </div>
              )}

              <div style={{
                maxWidth: "78%",
                padding: "10px 14px",
                borderRadius: msg.role === "user" ? "14px 4px 14px 14px" : "4px 14px 14px 14px",
                background: msg.role === "user"
                  ? "linear-gradient(135deg,#3b82f6,#1d4ed8)"
                  : msg.isError ? "#fef2f2" : "var(--surface-2,#f4f6fa)",
                color: msg.role === "user" ? "#fff"
                  : msg.isError ? "#dc2626" : "var(--text-primary,#0a0a0b)",
                fontSize: 13,
              }}>
                {msg.role === "assistant" && !msg.isError ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                    {msg.content}
                  </ReactMarkdown>
                ) : (
                  <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{msg.content}</span>
                )}

                {msg.sources > 0 && (
                  <div style={{ fontSize: 10, marginTop: 6, opacity: 0.55, fontStyle: "italic" }}>
                    {msg.sources} chunk(s) de contexte RAG utilisés
                  </div>
                )}
              </div>

              {msg.role === "user" && (
                <div style={{
                  width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                  background: "#3b82f6",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                  <User size={14} color="#fff" />
                </div>
              )}
            </div>

            {/* Inline graph — shown below the assistant bubble */}
            {msg.role === "assistant" && msg.graphData && (
              <div style={{ width: "calc(100% - 36px)", marginLeft: 36 }}>
                <InlineGraph graphData={msg.graphData} />
              </div>
            )}
          </div>
        ))}

        {loading && (
          <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
            <div style={{
              width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
              background: "linear-gradient(135deg,#a78bfa,#7c3aed)",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <Bot size={14} color="#fff" />
            </div>
            <div style={{ padding: "10px 14px", borderRadius: "4px 14px 14px 14px", background: "var(--surface-2,#f4f6fa)" }}>
              <TypingIndicator />
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* Suggestion chips — only on greeting */}
      {messages.length === 1 && !loading && (
        <div style={{
          padding: "6px 12px 0", flexShrink: 0,
          display: "flex", gap: 6, flexWrap: "wrap",
          borderTop: "1px solid var(--border,#e4e8f0)",
        }}>
          {SUGGESTIONS.slice(0, 3).map((s, i) => (
            <button key={i} onClick={() => send(s)} style={{
              padding: "4px 10px", fontSize: 11, cursor: "pointer",
              background: "var(--surface-2,#f4f6fa)",
              border: "1px solid var(--surface-4,#e4e8f0)",
              borderRadius: 12, color: "var(--text-secondary,#3f3f46)",
              transition: "background 150ms",
            }}>
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <div style={{
        padding: "12px 14px", flexShrink: 0,
        borderTop: "1px solid var(--border,#e4e8f0)",
        display: "flex", gap: 8, alignItems: "flex-end",
      }}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
          placeholder="Posez une question… Shift+Entrée pour saut de ligne"
          rows={1}
          style={{
            flex: 1, padding: "8px 12px", borderRadius: 8,
            border: "1px solid var(--border,#e4e8f0)",
            fontSize: 13, resize: "none", lineHeight: 1.5,
            background: "var(--surface-1,#fafafa)",
            color: "var(--text-primary)",
            outline: "none", maxHeight: 100, overflowY: "auto",
          }}
          disabled={loading}
        />
        <button
          onClick={() => send()}
          disabled={loading || !input.trim()}
          style={{
            padding: "8px 14px", borderRadius: 8, cursor: "pointer",
            background: loading || !input.trim() ? "var(--surface-3,#eef1f7)" : "#3b82f6",
            color: loading || !input.trim() ? "var(--text-tertiary)" : "#fff",
            border: "none", display: "flex", alignItems: "center", gap: 6,
            fontWeight: 600, fontSize: 13, transition: "all 150ms",
          }}
        >
          {loading
            ? <Loader2 size={15} style={{ animation: "spin 1s linear infinite" }} />
            : <Send size={15} />}
        </button>
      </div>
    </div>
  );
}
