import { FileText, Copy, Check, Download, ChevronRight, Printer } from "lucide-react";
import { useState, useMemo, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { printElement } from "../../utils/printReport";

/* ── Strategy colour map ─────────────────────────────────────────────────── */
const STRATEGY_COLORS = {
  rehost:     { bg: "#dbeafe", color: "#1d4ed8", border: "#bfdbfe" },
  replatform: { bg: "#e0e7ff", color: "#4338ca", border: "#c7d2fe" },
  refactor:   { bg: "#fce7f3", color: "#9d174d", border: "#fbcfe8" },
  retire:     { bg: "#fee2e2", color: "#991b1b", border: "#fecaca" },
  retain:     { bg: "#f3f4f6", color: "#374151", border: "#e5e7eb" },
  repurchase: { bg: "#d1fae5", color: "#065f46", border: "#a7f3d0" },
  relocate:   { bg: "#fef3c7", color: "#92400e", border: "#fde68a" },
};

function strategyBadge(word) {
  const key = word.toLowerCase();
  const style = STRATEGY_COLORS[key];
  if (!style) return null;
  return (
    <span style={{
      display: "inline-block",
      padding: "1px 8px", borderRadius: 12, fontSize: 11, fontWeight: 700,
      background: style.bg, color: style.color, border: `1px solid ${style.border}`,
      marginLeft: 4, verticalAlign: "middle", textTransform: "capitalize",
    }}>
      {word}
    </span>
  );
}

/* ── Extract headings for TOC ────────────────────────────────────────────── */
function extractHeadings(md) {
  const lines = md.split("\n");
  return lines
    .filter(l => /^#{1,3}\s/.test(l))
    .map(l => {
      const m = l.match(/^(#{1,3})\s+(.+)/);
      if (!m) return null;
      return {
        level: m[1].length,
        text: m[2].replace(/\*\*/g, "").trim(),
        id: m[2].toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
      };
    })
    .filter(Boolean);
}

/* ── Download as .md ─────────────────────────────────────────────────────── */
function downloadMd(report) {
  const blob = new Blob([report], { type: "text/markdown" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url;
  a.download = "migration-report.md";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/* ── Inline strategy badge renderer ─────────────────────────────────────── */
function enrichText(text) {
  if (typeof text !== "string") return text;
  const strategies = Object.keys(STRATEGY_COLORS);
  const re = new RegExp(`\\b(${strategies.join("|")})\\b`, "gi");
  const parts = text.split(re);
  return parts.map((p, i) => {
    const badge = strategyBadge(p);
    return badge ? <span key={i}>{badge}</span> : p;
  });
}

/* ── Main component ──────────────────────────────────────────────────────── */
export default function PreviewReport({ report }) {
  const [copied,  setCopied]  = useState(false);
  const [showToc, setShowToc] = useState(true);
  const bodyRef = useRef(null);

  const headings = useMemo(() => extractHeadings(report || ""), [report]);

  if (!report) return (
    <div style={{ textAlign: "center", padding: "48px 0", color: "var(--text-tertiary)" }}>
      <FileText size={36} style={{ margin: "0 auto 12px", display: "block", opacity: 0.3 }} />
      <div style={{ fontSize: 14 }}>Aucun rapport disponible.</div>
    </div>
  );

  const handleCopy = () => {
    navigator.clipboard.writeText(report).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: showToc && headings.length > 0 ? "220px 1fr" : "1fr", gap: 20, alignItems: "start" }}>

      {/* ── Table of contents ── */}
      {showToc && headings.length > 0 && (
        <div style={{
          position: "sticky", top: 16,
          background: "var(--surface-1, #f8fafc)",
          border: "1px solid var(--surface-3, #e2e8f0)",
          borderRadius: 12, padding: "14px 16px",
          fontSize: 12,
        }}>
          <div style={{ fontWeight: 700, color: "var(--text-primary)", marginBottom: 10, fontSize: 11.5, textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Table des matières
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {headings.map((h, i) => (
              <a
                key={i}
                href={`#${h.id}`}
                style={{
                  display: "flex", alignItems: "center", gap: 4,
                  paddingLeft: (h.level - 1) * 12,
                  color: "var(--text-secondary)",
                  textDecoration: "none", padding: "3px 6px",
                  borderRadius: 5, fontSize: 11.5,
                  transition: "background 120ms",
                }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--surface-2, #f1f5f9)"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}
              >
                <ChevronRight size={10} style={{ flexShrink: 0, opacity: 0.4 }} />
                {h.text}
              </a>
            ))}
          </div>
        </div>
      )}

      {/* ── Report body ── */}
      <div style={{
        background: "#fff",
        border: "1px solid var(--surface-3, #e2e8f0)",
        borderRadius: 14,
        overflow: "hidden",
        boxShadow: "0 2px 12px rgba(0,0,0,0.04)",
      }}>
        {/* Header bar */}
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "14px 20px",
          background: "linear-gradient(135deg, #f8fafc, #f1f5f9)",
          borderBottom: "1px solid var(--surface-3, #e2e8f0)",
        }}>
          <div style={{
            width: 34, height: 34, borderRadius: 9, flexShrink: 0,
            background: "linear-gradient(135deg, #6366f1, #0ea5e9)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <FileText size={16} color="#fff" />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 700, fontSize: 14, color: "var(--text-primary)" }}>
              Rapport de prévisualisation
            </div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 1 }}>
              Plan de migration généré par CloudShift AI
            </div>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {headings.length > 0 && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setShowToc(v => !v)}
                title="Afficher/masquer la table des matières"
                style={{ fontSize: 11 }}
              >
                {showToc ? "Masquer TOC" : "Afficher TOC"}
              </button>
            )}
            <button className="btn btn-ghost btn-sm" onClick={() => printElement(bodyRef.current, "Rapport de prévisualisation")} title="Télécharger en PDF">
              <Printer size={13} /> PDF
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => downloadMd(report)} title="Télécharger en Markdown">
              <Download size={13} />
            </button>
            <button className="btn btn-ghost btn-sm" onClick={handleCopy}>
              {copied ? <Check size={13} style={{ color: "#22c55e" }} /> : <Copy size={13} />}
              {copied ? "Copié !" : "Copier"}
            </button>
          </div>
        </div>

        {/* Markdown content */}
        <div ref={bodyRef} style={{ padding: "24px 28px", lineHeight: 1.75, fontSize: 14, color: "var(--text-primary, #1e293b)" }}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({ children }) => (
                <h1 id={String(children).toLowerCase().replace(/[^a-z0-9]+/g, "-")} style={{
                  fontSize: 22, fontWeight: 800, margin: "0 0 16px",
                  paddingBottom: 10, borderBottom: "2px solid var(--surface-3, #e2e8f0)",
                  color: "var(--text-primary)",
                  background: "linear-gradient(135deg, #3730a3, #0284c7)",
                  WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", backgroundClip: "text",
                }}>
                  {children}
                </h1>
              ),
              h2: ({ children }) => (
                <h2 id={String(children).toLowerCase().replace(/[^a-z0-9]+/g, "-")} style={{
                  fontSize: 16, fontWeight: 700, margin: "28px 0 10px",
                  paddingLeft: 10, borderLeft: "3px solid #6366f1",
                  color: "#3730a3",
                }}>
                  {children}
                </h2>
              ),
              h3: ({ children }) => (
                <h3 id={String(children).toLowerCase().replace(/[^a-z0-9]+/g, "-")} style={{
                  fontSize: 14, fontWeight: 700, margin: "18px 0 6px",
                  color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 6,
                }}>
                  <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#0ea5e9", display: "inline-block", flexShrink: 0 }} />
                  {children}
                </h3>
              ),
              table: ({ children }) => (
                <div style={{ overflowX: "auto", margin: "14px 0", borderRadius: 10, border: "1px solid var(--surface-3, #e2e8f0)" }}>
                  <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
                    {children}
                  </table>
                </div>
              ),
              thead: ({ children }) => (
                <thead style={{ background: "linear-gradient(135deg, #f8fafc, #f1f5f9)" }}>{children}</thead>
              ),
              th: ({ children }) => (
                <th style={{
                  padding: "9px 14px", textAlign: "left",
                  borderBottom: "2px solid var(--surface-3, #e2e8f0)",
                  fontWeight: 700, fontSize: 11.5, color: "var(--text-tertiary)",
                  textTransform: "uppercase", letterSpacing: "0.05em", whiteSpace: "nowrap",
                }}>
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td style={{
                  padding: "8px 14px",
                  borderBottom: "1px solid var(--surface-2, #f1f5f9)",
                  fontSize: 13,
                }}>
                  {typeof children === "string" ? enrichText(children) : children}
                </td>
              ),
              ul: ({ children }) => (
                <ul style={{ margin: "6px 0 10px", paddingLeft: 0, listStyle: "none" }}>{children}</ul>
              ),
              ol: ({ children }) => (
                <ol style={{ margin: "6px 0 10px", paddingLeft: 20 }}>{children}</ol>
              ),
              li: ({ children }) => (
                <li style={{
                  display: "flex", alignItems: "flex-start", gap: 8,
                  margin: "4px 0", fontSize: 13.5,
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#6366f1", marginTop: 7, flexShrink: 0 }} />
                  <span>{children}</span>
                </li>
              ),
              p: ({ children }) => (
                <p style={{ margin: "8px 0", lineHeight: 1.75, color: "var(--text-primary, #1e293b)" }}>
                  {typeof children === "string" ? enrichText(children) : children}
                </p>
              ),
              strong: ({ children }) => (
                <strong style={{ fontWeight: 700, color: "var(--text-primary)" }}>{children}</strong>
              ),
              em: ({ children }) => (
                <em style={{ color: "#6366f1", fontStyle: "normal", fontWeight: 600 }}>{children}</em>
              ),
              code: ({ inline, children }) => inline ? (
                <code style={{
                  background: "rgba(99,102,241,0.08)", padding: "2px 6px",
                  borderRadius: 5, fontSize: 12, fontFamily: "monospace",
                  color: "#4338ca", border: "1px solid rgba(99,102,241,0.15)",
                }}>
                  {children}
                </code>
              ) : (
                <pre style={{
                  background: "#0f172a", color: "#e2e8f0",
                  padding: "16px 18px", borderRadius: 10, overflowX: "auto",
                  fontSize: 12.5, lineHeight: 1.65, margin: "12px 0",
                  fontFamily: "monospace",
                }}>
                  <code>{children}</code>
                </pre>
              ),
              blockquote: ({ children }) => (
                <blockquote style={{
                  margin: "12px 0", padding: "10px 16px",
                  background: "rgba(99,102,241,0.06)",
                  borderLeft: "4px solid #6366f1",
                  borderRadius: "0 8px 8px 0",
                  color: "#4338ca", fontSize: 13.5,
                }}>
                  {children}
                </blockquote>
              ),
              hr: () => (
                <div style={{ margin: "20px 0", height: 1, background: "linear-gradient(90deg, #6366f1, transparent)" }} />
              ),
            }}
          >
            {report}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
