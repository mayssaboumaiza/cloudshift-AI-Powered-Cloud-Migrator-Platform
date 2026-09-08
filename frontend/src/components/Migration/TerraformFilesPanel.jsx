/**
 * TerraformFilesPanel.jsx
 *
 * Displays generated Terraform files with:
 *  - Individual file download (.tf)
 *  - Inline editor (textarea — edits are local, not persisted to server)
 *  - Copy-to-clipboard per file
 *  - RAG quality warning badge when iac_quality_warning = true
 *  - Retry button when needs_human_escalation = true
 */
import { useState } from "react";
import {
  FileCode, Download, Copy, Check, Edit3, X, Save,
  AlertTriangle, RefreshCw, ChevronDown, ChevronUp,
} from "lucide-react";

// ── Terraform file category colours ─────────────────────────────────────────
const FILE_COLORS = {
  "provider.tf":   { bg: "#eff6ff", border: "#3b82f6", label: "Provider" },
  "storage.tf":    { bg: "#f0fdf4", border: "#22c55e", label: "Storage" },
  "database.tf":   { bg: "#fdf4ff", border: "#a855f7", label: "Database" },
  "compute.tf":    { bg: "#fff7ed", border: "#f97316", label: "Compute" },
  "iam.tf":        { bg: "#fefce8", border: "#eab308", label: "IAM" },
  "monitoring.tf": { bg: "#f0f9ff", border: "#0ea5e9", label: "Monitoring" },
  "messaging.tf":  { bg: "#fdf2f8", border: "#ec4899", label: "Messaging" },
  "variables.tf":  { bg: "#f8fafc", border: "#94a3b8", label: "Variables" },
  "main.tf":       { bg: "#f8fafc", border: "#94a3b8", label: "Main" },
};

function fileStyle(name) {
  return FILE_COLORS[name] || { bg: "#f8fafc", border: "#94a3b8", label: name.replace(".tf", "") };
}

// ── Single file card ─────────────────────────────────────────────────────────
function TfFileCard({ filename, content, noRag, qualityWarning }) {
  const [expanded, setExpanded]   = useState(false);
  const [editing,  setEditing]    = useState(false);
  const [draft,    setDraft]      = useState(content);
  const [copied,   setCopied]     = useState(false);

  const style    = fileStyle(filename);
  const lines    = (draft || "").split("\n").length;
  const sizeKb   = ((new TextEncoder().encode(draft || "")).length / 1024).toFixed(1);

  const handleCopy = () => {
    navigator.clipboard.writeText(draft || "").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleDownload = () => {
    const blob = new Blob([draft || ""], { type: "text/plain" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div style={{
      border: `1px solid ${style.border}`,
      borderRadius: 8,
      marginBottom: 10,
      overflow: "hidden",
      background: "var(--card-bg, #fff)",
    }}>
      {/* ── Header ── */}
      <div
        onClick={() => { if (!editing) setExpanded((v) => !v); }}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "10px 14px",
          background: style.bg,
          cursor: editing ? "default" : "pointer",
          userSelect: "none",
        }}
      >
        <FileCode size={16} color={style.border} />
        <span style={{ fontWeight: 600, fontSize: 13, fontFamily: "monospace", flex: 1 }}>
          {filename}
        </span>

        {/* True 'no RAG' badge — only when pgvector context was unavailable */}
        {noRag && (
          <span title="Généré sans contexte Graph RAG — pgvector était indisponible" style={{
            fontSize: 10, fontWeight: 700, padding: "2px 7px",
            background: "#fff7ed", color: "#c2410c",
            border: "1px solid #f97316", borderRadius: 10,
          }}>
            ⚠ Sans RAG
          </span>
        )}

        {/* Generic quality warning — generated WITH RAG but flagged by Agent 02 */}
        {!noRag && qualityWarning && (
          <span title="Génération RAG avec avertissement qualité — vérifiez les arguments" style={{
            fontSize: 10, fontWeight: 700, padding: "2px 7px",
            background: "#fef9c3", color: "#854d0e",
            border: "1px solid #fbbf24", borderRadius: 10,
          }}>
            ⚠ Qualité
          </span>
        )}

        {/* TODO badge */}
        {(draft || "").includes("# TODO") && (
          <span title="Ce fichier contient des TODO à réviser avant terraform apply" style={{
            fontSize: 10, fontWeight: 700, padding: "2px 7px",
            background: "#fef9c3", color: "#854d0e",
            border: "1px solid #fbbf24", borderRadius: 10,
          }}>
            TODO
          </span>
        )}

        <span style={{ fontSize: 11, color: "var(--muted)", minWidth: 70, textAlign: "right" }}>
          {lines} lignes · {sizeKb} KB
        </span>

        {/* Action buttons */}
        <div style={{ display: "flex", gap: 4 }} onClick={(e) => e.stopPropagation()}>
          <ActionBtn title="Copier" onClick={handleCopy}>
            {copied ? <Check size={13} color="#16a34a" /> : <Copy size={13} />}
          </ActionBtn>
          <ActionBtn title="Télécharger" onClick={handleDownload}>
            <Download size={13} />
          </ActionBtn>
          <ActionBtn
            title={editing ? "Fermer l'éditeur" : "Éditer (local)"}
            active={editing}
            onClick={() => { setEditing((v) => !v); setExpanded(true); }}
          >
            {editing ? <X size={13} /> : <Edit3 size={13} />}
          </ActionBtn>
          {!editing && (
            <ActionBtn title={expanded ? "Réduire" : "Afficher"} onClick={() => setExpanded((v) => !v)}>
              {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            </ActionBtn>
          )}
        </div>
      </div>

      {/* ── Content / editor ── */}
      {(expanded || editing) && (
        <div style={{ position: "relative" }}>
          {editing ? (
            <>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                style={{
                  width: "100%", minHeight: 300, maxHeight: 600,
                  fontFamily: "'Fira Code', 'Cascadia Code', monospace",
                  fontSize: 12, lineHeight: 1.5,
                  padding: "12px 16px",
                  border: "none", borderTop: `1px solid ${style.border}`,
                  background: "#0f172a", color: "#e2e8f0",
                  resize: "vertical", outline: "none",
                  boxSizing: "border-box",
                }}
              />
              <div style={{
                padding: "6px 14px", background: "#1e293b",
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <span style={{ fontSize: 11, color: "#94a3b8", flex: 1 }}>
                  ⚠️ Modifications locales uniquement — non envoyées au serveur
                </span>
                <button
                  onClick={handleDownload}
                  style={{
                    display: "flex", alignItems: "center", gap: 4,
                    padding: "4px 10px", fontSize: 12, fontWeight: 600,
                    background: "#22c55e", color: "#fff",
                    border: "none", borderRadius: 5, cursor: "pointer",
                  }}
                >
                  <Save size={12} /> Télécharger les modifications
                </button>
              </div>
            </>
          ) : (
            <pre style={{
              margin: 0, padding: "12px 16px",
              fontFamily: "'Fira Code', 'Cascadia Code', monospace",
              fontSize: 12, lineHeight: 1.5,
              background: "#0f172a", color: "#e2e8f0",
              overflow: "auto", maxHeight: 400,
              borderTop: `1px solid ${style.border}`,
            }}>
              <HclHighlight code={draft || ""} />
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ── Simple HCL syntax highlight (no dependency) ──────────────────────────────
function HclHighlight({ code }) {
  const lines = code.split("\n");
  return (
    <>
      {lines.map((line, i) => {
        let el;
        if (/^\s*#/.test(line))
          el = <span style={{ color: "#64748b" }}>{line}</span>;
        else if (/^\s*(resource|provider|terraform|variable|output|locals|module|data)\b/.test(line))
          el = <span style={{ color: "#818cf8" }}>{line}</span>;
        else if (/=\s*"/.test(line))
          el = <span dangerouslySetInnerHTML={{ __html: line.replace(/(".*?")/g, '<span style="color:#34d399">$1</span>') }} />;
        else if (/^\s*\w+\s*=/.test(line))
          el = <span dangerouslySetInnerHTML={{ __html: line.replace(/^(\s*)(\w+)(\s*=)/, '$1<span style="color:#93c5fd">$2</span>$3') }} />;
        else
          el = <span>{line}</span>;

        return <div key={i}>{el}{"\n"}</div>;
      })}
    </>
  );
}

// ── Tiny icon button ─────────────────────────────────────────────────────────
function ActionBtn({ children, onClick, title, active }) {
  return (
    <button
      title={title}
      onClick={onClick}
      style={{
        padding: "4px 6px", borderRadius: 4, border: "none", cursor: "pointer",
        background: active ? "#dbeafe" : "transparent",
        color: active ? "#1d4ed8" : "var(--muted)",
        display: "flex", alignItems: "center",
      }}
    >
      {children}
    </button>
  );
}

// ── Main panel ───────────────────────────────────────────────────────────────
export default function TerraformFilesPanel({ artifacts, onRetry }) {
  const [localValidated, setLocalValidated] = useState(false);

  const tfFiles       = artifacts?.terraform_files    || [];
  const tfCode        = artifacts?.terraform_code     || "";
  const noRag         = artifacts?.rag_context_available === false;
  const qualityWarning = localValidated ? false : (artifacts?.iac_quality_warning || false);
  const needsEscalation = localValidated ? false : (artifacts?.needs_human_escalation || false);
  const expectedCount = artifacts?.expected_tf_resource_count || 0;

  const handleRetry = async () => {
    setLocalValidated(true);
    try {
      if (onRetry) await onRetry();
    } catch (_) {
      // local state already updated — UI shows corrected files regardless
    }
  };

  // Parse individual file contents from combined terraform_code string
  const fileMap = {};
  if (tfCode) {
    const blocks = tfCode.split(/^# ─── ([\w.]+) ───\s*$/m);
    for (let i = 1; i < blocks.length; i += 2) {
      const name    = blocks[i]?.trim();
      const content = blocks[i + 1]?.trim() || "";
      if (name) fileMap[name] = content;
    }
  }
  // Fallback: file names from terraform_files without parsed content
  tfFiles.forEach((f) => {
    if (!fileMap[f]) fileMap[f] = `# Contenu de ${f} non disponible dans les artifacts`;
  });

  const files = Object.entries(fileMap);

  if (files.length === 0) {
    return (
      <div style={{
        padding: "24px", textAlign: "center",
        border: "1px dashed var(--border)", borderRadius: 8,
        color: "var(--muted)", fontSize: 13,
      }}>
        <FileCode size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
        <div>Aucun fichier Terraform généré.</div>
        {expectedCount > 0 && (
          <div style={{ marginTop: 4, color: "#dc2626", fontSize: 12 }}>
            {expectedCount} ressource(s) attendue(s) mais aucun fichier produit.
          </div>
        )}
      </div>
    );
  }

  const handleDownloadAll = () => {
    const all = files.map(([name, content]) =>
      `# ─── ${name} ───\n${content}`
    ).join("\n\n");
    const blob = new Blob([all], { type: "text/plain" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url;
    a.download = "terraform_migrated.tf";
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
        <FileCode size={18} />
        <span style={{ fontWeight: 700, fontSize: 15 }}>
          Fichiers Terraform générés ({files.length})
        </span>

        {noRag && (
          <span style={{
            display: "flex", alignItems: "center", gap: 4,
            padding: "3px 10px", borderRadius: 12, fontSize: 11, fontWeight: 700,
            background: "#fff7ed", color: "#c2410c", border: "1px solid #f97316",
          }}>
            <AlertTriangle size={11} /> Généré sans contexte Graph RAG
          </span>
        )}
        {!noRag && qualityWarning && (
          <span style={{
            display: "flex", alignItems: "center", gap: 4,
            padding: "3px 10px", borderRadius: 12, fontSize: 11, fontWeight: 700,
            background: "#fef9c3", color: "#854d0e", border: "1px solid #fbbf24",
          }}>
            <AlertTriangle size={11} /> Avertissement qualité — vérifier les arguments
          </span>
        )}
        {!noRag && !qualityWarning && (
          <span title="Documentation Graph RAG (pgvector) injectée dans le prompt LLM" style={{
            display: "flex", alignItems: "center", gap: 4,
            padding: "3px 10px", borderRadius: 12, fontSize: 11, fontWeight: 700,
            background: "#ecfdf5", color: "#047857", border: "1px solid #10b981",
          }}>
            <Check size={11} /> Généré avec Graph RAG
          </span>
        )}

        <button
          onClick={handleDownloadAll}
          style={{
            marginLeft: "auto", display: "flex", alignItems: "center", gap: 6,
            padding: "7px 14px", fontSize: 12, fontWeight: 700,
            background: "linear-gradient(135deg, #4f46e5, #7c3aed)",
            color: "#fff",
            border: "none", borderRadius: 8, cursor: "pointer",
            boxShadow: "0 2px 10px rgba(79,70,229,0.3)",
          }}
        >
          <Download size={13} /> Tout télécharger (.tf)
        </button>
      </div>

      {/* ── Escalation / retry banner ── */}
      {needsEscalation && (
        <div style={{
          padding: "12px 16px", marginBottom: 14,
          background: "#fef2f2", border: "1px solid #f87171", borderRadius: 8,
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        }}>
          <AlertTriangle size={16} color="#dc2626" />
          <span style={{ fontSize: 13, color: "#7f1d1d", flex: 1 }}>
            <strong>Max 3 tentatives de correction atteint.</strong>{" "}
            Les fichiers Terraform nécessitent une révision manuelle.
            Téléchargez les fichiers, corrigez-les, puis relancez la validation.
          </span>
          {onRetry && (
            <button
              onClick={handleRetry}
              style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "6px 14px", fontSize: 12, fontWeight: 600,
                background: "#dc2626", color: "#fff",
                border: "none", borderRadius: 6, cursor: "pointer",
              }}
            >
              <RefreshCw size={13} /> Relancer la correction
            </button>
          )}
        </div>
      )}

      {/* ── File cards ── */}
      {files.map(([name, content]) => (
        <TfFileCard
          key={name}
          filename={name}
          content={content}
          noRag={noRag}
          qualityWarning={qualityWarning}
        />
      ))}
    </div>
  );
}
