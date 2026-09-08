import { X } from "lucide-react";

/* Inject slide-in keyframe once */
if (typeof document !== "undefined" && !document.getElementById("ndp-kf")) {
  const s = document.createElement("style");
  s.id = "ndp-kf";
  s.textContent = `
    @keyframes panel-slide-in {
      from { opacity: 0; transform: translateX(24px); }
      to   { opacity: 1; transform: translateX(0); }
    }
  `;
  document.head.appendChild(s);
}

const TYPE_LABELS = {
  compute: "Compute", storage: "Storage", database: "Database",
  network: "Network", iam: "Identity & Access", messaging: "Messaging",
  monitoring: "Monitoring", ai: "AI / ML", other: "Other",
};

const CLOUD_LABELS = { aws: "Amazon Web Services", gcp: "Google Cloud", azure: "Microsoft Azure" };

const COMPLEXITY_COLORS = {
  HIGH:   "#ef4444",
  MEDIUM: "#f59e0b",
  LOW:    "#22c55e",
};

export default function NodeDetailPanel({ node, onClose, artifacts }) {
  if (!node) return null;
  const { data } = node;
  const isTarget = data.role === "target";
  const resource = data.resource ?? {};
  const tfSnippet = isTarget ? findTfSnippet(data.label, artifacts) : null;

  return (
    <div
      style={{
        position: "absolute",
        right: 0, top: 0, bottom: 0,
        width: 350,
        background: "var(--surface-0, #fff)",
        borderLeft: "1px solid var(--surface-4, #e4e8f0)",
        boxShadow: "-6px 0 32px rgba(0,0,0,0.10)",
        zIndex: 20,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        fontFamily: "var(--font-sans, system-ui)",
        animation: "panel-slide-in 200ms cubic-bezier(0.16,1,0.3,1) both",
      }}
    >
      {/* ── Header ── */}
      <div
        style={{
          padding: "13px 14px",
          borderBottom: "1px solid var(--surface-3, #eef1f7)",
          background: "var(--surface-1, #fafafa)",
          display: "flex",
          alignItems: "center",
          gap: 10,
          flexShrink: 0,
        }}
      >
        <span
          style={{
            width: 10, height: 10, borderRadius: "50%",
            background: data.color, flexShrink: 0,
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13, fontWeight: 700, color: "var(--text-primary, #0a0a0b)",
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}
            title={data.label}
          >
            {data.label}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary, #71717a)", marginTop: 2 }}>
            {TYPE_LABELS[data.type] ?? data.type} ·{" "}
            {CLOUD_LABELS[data.cloud] ?? data.cloud?.toUpperCase() ?? "Unknown cloud"}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: "none", border: "none", cursor: "pointer",
            color: "var(--text-tertiary, #71717a)", padding: 4, borderRadius: 6,
            display: "flex", flexShrink: 0, transition: "background 120ms",
          }}
        >
          <X size={16} />
        </button>
      </div>

      {/* ── Body ── */}
      <div style={{ flex: 1, overflowY: "auto", padding: "14px 14px" }}>
        {isTarget
          ? <TargetDetail data={data} resource={resource} tfSnippet={tfSnippet} />
          : <SourceDetail data={data} />
        }
      </div>

      {/* ── Footer role chip ── */}
      <div
        style={{
          padding: "10px 14px",
          borderTop: "1px solid var(--surface-3, #eef1f7)",
          background: "var(--surface-1, #fafafa)",
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 11, color: "var(--text-tertiary, #71717a)",
        }}
      >
        <span
          style={{
            padding: "2px 8px", borderRadius: 8,
            background: isTarget ? `${data.cloudColor ?? "#94a3b8"}18` : "#3b82f618",
            color: isTarget ? (data.cloudColor ?? "#94a3b8") : "#3b82f6",
            fontWeight: 700, fontSize: 10,
          }}
        >
          {isTarget ? "TARGET" : "SOURCE"}
        </span>
        {data.strategy && (
          <span style={{ fontSize: 10, color: data.color, fontWeight: 600 }}>
            {data.strategy}
          </span>
        )}
        <span style={{ marginLeft: "auto" }}>Click outside to close</span>
      </div>
    </div>
  );
}

/* ── Source node detail ───────────────────────────────────────────────────── */
function SourceDetail({ data }) {
  return (
    <>
      <Section title="Detection">
        <Row label="Service ID"   value={data.label} mono />
        <Row label="Type"         value={TYPE_LABELS[data.type] ?? data.type} />
        <Row label="Cloud"        value={data.cloud?.toUpperCase()} />
        <Row
          label="Détection"
          value={
            data.detectionSource === "user_selection"
              ? "Manuel (sélection utilisateur)"
              : data.cloudConfirmed
                ? "IaC — confirmé"
                : "IaC — inféré"
          }
        />
        {data.detectionSource && data.detectionSource !== "user_selection" && (
          <Row label="Origine" value={data.detectionSource} />
        )}
      </Section>

      <Section title="Quality metrics">
        {data.complexity && (
          <Row
            label="Complexity"
            value={data.complexity}
            badgeColor={COMPLEXITY_COLORS[data.complexity] ?? "#94a3b8"}
          />
        )}
        {typeof data.score === "number" && (
          <Row
            label="Score"
            value={data.score % 1 === 0 ? String(data.score) : data.score.toFixed(2)}
          />
        )}
      </Section>
    </>
  );
}

/* ── Target node detail ───────────────────────────────────────────────────── */
function TargetDetail({ data, resource, tfSnippet }) {
  const score = resource.composite_score ?? resource.score;

  return (
    <>
      <Section title="Migration mapping">
        <Row label="Source"          value={resource.source_service ?? resource.service ?? "—"} mono />
        <Row label="Target"          value={data.label} mono />
        <Row label="Strategy"        value={data.strategy ?? "—"} badgeColor={data.color} />
        <Row label="Target cloud"    value={data.cloud?.toUpperCase()} />
        {typeof score === "number" && (
          <Row label="Composite score" value={`${(score * 100).toFixed(0)}%`} />
        )}
      </Section>

      <Section title="Terraform mapping">
        {tfSnippet ? (
          <pre
            style={{
              margin: 0,
              padding: "10px 11px",
              background: "var(--surface-2, #f4f6fa)",
              border: "none",
              borderRadius: 0,
              fontSize: 11,
              fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
              color: "var(--text-secondary, #3f3f46)",
              overflowX: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
              maxHeight: 260,
              overflowY: "auto",
              lineHeight: 1.55,
            }}
          >
            {tfSnippet}
          </pre>
        ) : (
          <div style={{ padding: "10px 11px", fontSize: 12, color: "var(--text-tertiary, #71717a)" }}>
            No Terraform block found for this service.
          </div>
        )}
      </Section>

      {resource.rejection_reason && (
        <Section title="Partial rejection">
          <div
            style={{
              padding: "8px 10px", fontSize: 12,
              color: "var(--text-secondary, #3f3f46)",
              background: "#fffbeb",
              borderRadius: 0,
            }}
          >
            {resource.rejection_reason}
          </div>
        </Section>
      )}
    </>
  );
}

/* ── Shared sub-components ───────────────────────────────────────────────── */
function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div
        style={{
          fontSize: 9, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: 0.9, color: "var(--text-tertiary, #71717a)", marginBottom: 6,
        }}
      >
        {title}
      </div>
      <div
        style={{
          background: "var(--surface-1, #fafafa)",
          borderRadius: 8,
          border: "1px solid var(--surface-3, #eef1f7)",
          overflow: "hidden",
        }}
      >
        {children}
      </div>
    </div>
  );
}

function Row({ label, value, mono, badgeColor }) {
  return (
    <div
      style={{
        display: "flex", alignItems: "center",
        padding: "7px 10px",
        borderBottom: "1px solid var(--surface-2, #f4f6fa)",
        gap: 8,
        fontSize: 12,
      }}
    >
      <span style={{ flex: "0 0 104px", color: "var(--text-tertiary, #71717a)", fontSize: 11 }}>
        {label}
      </span>
      {badgeColor ? (
        <span
          style={{
            fontSize: 11, fontWeight: 700,
            padding: "2px 8px", borderRadius: 8,
            background: `${badgeColor}20`, color: badgeColor,
          }}
        >
          {value ?? "—"}
        </span>
      ) : (
        <span
          style={{
            flex: 1, fontWeight: 500, color: "var(--text-primary, #0a0a0b)",
            fontFamily: mono ? "var(--font-mono, 'JetBrains Mono', monospace)" : undefined,
            fontSize: mono ? 11 : 12,
            wordBreak: "break-word",
          }}
        >
          {value ?? "—"}
        </span>
      )}
    </div>
  );
}

/* ── Terraform snippet extraction ─────────────────────────────────────────── */
function findTfSnippet(serviceName, artifacts) {
  if (!serviceName || !artifacts) return null;

  const tfFiles = artifacts.terraform_files ?? [];
  const tfCode = artifacts.terraform_code ?? "";
  const corpus = typeof tfCode === "string" ? tfCode : JSON.stringify(tfCode);

  const slug = serviceName.toLowerCase().replace(/[^a-z0-9]/g, "_").replace(/_+/g, "_");
  const allCode = [corpus, ...tfFiles.map((f) => f.content ?? f.code ?? "")].join("\n");

  if (!allCode.trim()) return null;

  const pattern = new RegExp(
    `resource\\s+"[^"]*"\\s+"[^"]*${slug}[^"]*"\\s*\\{[\\s\\S]{10,600}?\\}`,
    "im"
  );
  const match = allCode.match(pattern);
  if (match) return match[0].length > 600 ? match[0].slice(0, 600) + "\n  …" : match[0];

  for (const f of tfFiles) {
    const fname = (f.filename ?? f.name ?? "").toLowerCase();
    if (fname.includes(slug)) {
      const block = (f.content ?? "").match(/resource\s+"[^"]+"\s+"[^"]+"\s*\{[\s\S]{10,400}?\}/)?.[0];
      if (block) return block.length > 600 ? block.slice(0, 600) + "\n  …" : block;
    }
  }
  return null;
}
