import { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  PlusCircle, ArrowRight, Trash2, RefreshCw, Search, Inbox,
  Layers, Activity, CheckCircle, AlertCircle, Clock, Cloud,
  GitBranch, Rocket, Filter, LayoutGrid, List, TrendingUp,
  Zap, Shield, DollarSign, ChevronRight, Terminal, Cpu,
} from "lucide-react";
import { fetchMigrations, deleteMigration } from "../api/migrationApi";
import StatusBadge from "../components/common/StatusBadge";
import Alert from "../components/common/Alert";
import { useI18n } from "../context/I18nContext";

/* ── Provider badge ─────────────────────────────────────────────────────── */
const PROVIDER_COLORS = {
  aws:   { color: "#FF9900", bg: "#FFF7ED", border: "#FED7AA", label: "AWS" },
  gcp:   { color: "#4285F4", bg: "#EFF6FF", border: "#BFDBFE", label: "GCP" },
  azure: { color: "#0078D4", bg: "#F0F9FF", border: "#BAE6FD", label: "Azure" },
};
function CloudBadge({ provider }) {
  const p = PROVIDER_COLORS[provider?.toLowerCase()] || { color: "#6366f1", bg: "#f5f3ff", border: "#ddd6fe", label: provider?.toUpperCase() || "?" };
  return (
    <span style={{
      padding: "2px 8px", borderRadius: 6, fontSize: 10.5, fontWeight: 700,
      background: p.bg, color: p.color, border: `1px solid ${p.border}`,
    }}>{p.label}</span>
  );
}

/* ── Status config ─────────────────────────────────────────────────────── */
const STATUS_PROGRESS = {
  Analyzing: 14, Generating_IaC: 42, Validating_Intent: 56,
  Correcting: 50, IaC_Ready: 70, Accepted: 72,
  Deploying: 85, Health_Checking: 92, Completed: 100, Exported: 100,
};
const ACTIVE_STATUSES = new Set([
  "Analyzing","Generating_IaC","Validating_Intent","Deploying","Health_Checking","Correcting",
]);
const PIPELINE_PHASE_COLORS = {
  Analyzing:         { color: "#0EA5E9", icon: "🔍", key: "dashboard.phase.scan" },
  Generating_IaC:    { color: "#7C3AED", icon: "⚙️", key: "dashboard.phase.iac" },
  Validating_Intent: { color: "#F59E0B", icon: "🛡",  key: "dashboard.phase.validation" },
  Correcting:        { color: "#F97316", icon: "🔧", key: "dashboard.phase.correction" },
  Deploying:         { color: "#10B981", icon: "🚀", key: "dashboard.phase.deploy" },
  Health_Checking:   { color: "#06B6D4", icon: "💓", key: "dashboard.phase.health" },
  Completed:         { color: "#16A34A", icon: "✅", key: "dashboard.phase.done" },
  Exported:          { color: "#16A34A", icon: "📦", key: "dashboard.phase.exported" },
  Failed:            { color: "#DC2626", icon: "❌", key: "dashboard.phase.failed" },
  Analysis_Failed:   { color: "#DC2626", icon: "❌", key: "dashboard.phase.analysis_failed" },
  Plan_Ready:        { color: "#D97706", icon: "👀", key: "dashboard.phase.plan_ready" },
  Reviewing:         { color: "#D97706", icon: "👁",  key: "dashboard.phase.reviewing" },
  Accepted:          { color: "#0EA5E9", icon: "✓",  key: "dashboard.phase.accepted" },
};

/* ── Impact metrics — env0 style ─────────────────────────────────────────── */
function MetricsRow({ migrations }) {
  const { t } = useI18n();
  const total     = migrations.length;
  const running   = migrations.filter(m => ACTIVE_STATUSES.has(m.status)).length;
  const completed = migrations.filter(m => ["Completed","Exported"].includes(m.status)).length;
  const failed    = migrations.filter(m => ["Failed","Analysis_Failed"].includes(m.status)).length;
  const pending   = migrations.filter(m => ["Plan_Ready","Reviewing","Accepted"].includes(m.status)).length;
  const successRate = total > 0 ? Math.round((completed / total) * 100) : 0;

  const cards = [
    {
      icon: <Layers size={22} strokeWidth={1.6} />,
      value: total, label: t("dashboard.metric.total"),
      sub: t("dashboard.metric.sub.all"), color: "#6366F1", bg: "rgba(99,102,241,0.06)",
    },
    {
      icon: <Activity size={22} strokeWidth={1.6} />,
      value: running, label: t("dashboard.metric.running"),
      sub: running > 0 ? t("dashboard.metric.sub.active") : t("dashboard.metric.sub.none"),
      color: "#0EA5E9", bg: "rgba(14,165,233,0.06)",
      pulse: running > 0,
    },
    {
      icon: <CheckCircle size={22} strokeWidth={1.6} />,
      value: completed, label: t("dashboard.metric.completed"),
      sub: t("dashboard.metric.sub.success", { n: successRate }), color: "#16A34A", bg: "rgba(22,163,74,0.06)",
    },
    {
      icon: <Clock size={22} strokeWidth={1.6} />,
      value: pending, label: t("dashboard.metric.pending"),
      sub: t("dashboard.metric.sub.pending"), color: "#D97706", bg: "rgba(217,119,6,0.06)",
    },
    {
      icon: <AlertCircle size={22} strokeWidth={1.6} />,
      value: failed, label: t("dashboard.metric.failed"),
      sub: failed > 0 ? t("dashboard.metric.sub.failed") : t("dashboard.metric.sub.ok"),
      color: failed > 0 ? "#DC2626" : "#9CA3AF", bg: failed > 0 ? "rgba(220,38,38,0.06)" : "rgba(156,163,175,0.06)",
    },
  ];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 12, marginBottom: 24 }}>
      {cards.map((c, i) => (
        <motion.div
          key={c.label}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.06, duration: 0.22, ease: [0.16,1,0.3,1] }}
          style={{
            padding: "16px 18px", borderRadius: 12,
            background: "var(--surface-0)", border: "1px solid var(--surface-4)",
            position: "relative", overflow: "hidden",
            boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}
        >
          {/* color accent top bar */}
          <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: c.color, opacity: 0.7 }} />
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 10 }}>
            <div style={{
              width: 38, height: 38, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center",
              background: c.bg, color: c.color, flexShrink: 0,
            }}>
              {c.icon}
            </div>
            {c.pulse && (
              <span style={{
                width: 8, height: 8, borderRadius: "50%", background: c.color,
                boxShadow: `0 0 0 3px ${c.color}30`,
                animation: "pulse-dot 1.5s ease-in-out infinite",
              }} />
            )}
          </div>
          <div style={{ fontSize: 28, fontWeight: 800, color: "var(--text-primary)", lineHeight: 1 }}>{c.value}</div>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginTop: 4 }}>{c.label}</div>
          <div style={{ fontSize: 10, color: "var(--text-disabled)", marginTop: 2 }}>{c.sub}</div>
        </motion.div>
      ))}
    </div>
  );
}


/* ── AI Banner — Pulumi-inspired ─────────────────────────────────────────── */
function AIBanner({ running }) {
  const { t } = useI18n();
  if (running === 0) return null;
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      style={{
        marginBottom: 20, padding: "12px 18px",
        background: "linear-gradient(135deg, rgba(99,102,241,0.08), rgba(14,165,233,0.08))",
        border: "1px solid rgba(99,102,241,0.2)", borderRadius: 12,
        display: "flex", alignItems: "center", gap: 12,
      }}
    >
      <div style={{
        width: 34, height: 34, borderRadius: 10, flexShrink: 0,
        background: "linear-gradient(135deg, #6366f1, #0ea5e9)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <Cpu size={16} color="#fff" />
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: "#4f46e5" }}>
          {t("dashboard.ai.running", { n: running, s: running > 1 ? "s" : "" })}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 1 }}>
          Migration Planner → IaC Generator → Deploy Orchestrator · GraphRAG · Terraform IaC · Auto-correction
        </div>
      </div>
      <div style={{ display: "flex", gap: 6 }}>
        {["Migration Planner","GraphRAG","Terraform","Deploy Orchestrator"].map((s, i) => (
          <span key={s} style={{
            padding: "3px 8px", borderRadius: 6, fontSize: 10, fontWeight: 600,
            background: "rgba(99,102,241,0.1)", color: "#6366f1",
            border: "1px solid rgba(99,102,241,0.2)",
            animation: `pulse-dot ${1.5 + i * 0.2}s ease-in-out infinite`,
          }}>
            {s}
          </span>
        ))}
      </div>
    </motion.div>
  );
}

/* ── Skeleton ────────────────────────────────────────────────────────────── */
function Skeleton({ h = 16, w = "100%", r = 6 }) {
  return <div style={{ height: h, width: w, borderRadius: r, background: "var(--surface-3)", animation: "skeleton-pulse 1.5s ease-in-out infinite" }} />;
}

/* ── Grid card — Brainboard-inspired visual ──────────────────────────────── */
function MigrationCard({ m, onDelete, onClick }) {
  const { t } = useI18n();
  const isActive    = ACTIVE_STATUSES.has(m.status);
  const isCompleted = ["Completed","Exported"].includes(m.status);
  const isFailed    = ["Failed","Analysis_Failed"].includes(m.status);
  const isPending   = ["Plan_Ready","Reviewing","Accepted"].includes(m.status);
  const progress    = STATUS_PROGRESS[m.status] ?? null;
  const phaseConf   = PIPELINE_PHASE_COLORS[m.status];
  const phase       = phaseConf ? { ...phaseConf, label: t(phaseConf.key) } : null;

  const now = new Date();
  const createdDate = new Date(m.created_at);
  const diffH = Math.round((now - createdDate) / 3600000);
  const timeAgo = diffH < 1 ? t("dashboard.instant") : diffH < 24 ? `${diffH}h` : `${Math.round(diffH / 24)}j`;

  const borderColor = isCompleted ? "#16A34A" : isFailed ? "#DC2626" : isActive ? "#6366F1" : isPending ? "#D97706" : "var(--surface-4)";

  return (
    <motion.div
      layout
      whileHover={{ y: -2, boxShadow: "0 8px 24px rgba(0,0,0,0.08)" }}
      onClick={onClick}
      style={{
        background: "var(--surface-0)", borderRadius: 12, cursor: "pointer",
        border: `1px solid var(--surface-4)`, borderTop: `3px solid ${borderColor}`,
        overflow: "hidden", transition: "box-shadow 200ms ease",
        display: "flex", flexDirection: "column",
      }}
    >
      {/* Active progress bar */}
      {isActive && progress != null && (
        <div style={{ height: 2, background: "var(--surface-3)" }}>
          <div style={{
            height: "100%", width: `${progress}%`,
            background: "linear-gradient(90deg, #6366f1, #0EA5E9)",
            transition: "width 1s ease",
          }} />
        </div>
      )}

      <div style={{ padding: "14px 16px", flex: 1 }}>
        {/* Header row */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
          <StatusBadge status={m.status} />
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{
              padding: "1px 6px", borderRadius: 10, fontSize: 9, fontWeight: 700,
              background: "rgba(99,102,241,0.08)", color: "#6366f1", border: "1px solid rgba(99,102,241,0.15)",
            }}>✦ AI</span>
            <button
              style={{ background: "none", border: "none", cursor: "pointer", padding: 3, borderRadius: 5, color: "var(--text-disabled)" }}
              onClick={e => { e.stopPropagation(); onDelete(); }}
              onMouseEnter={e => e.currentTarget.style.color = "#dc2626"}
              onMouseLeave={e => e.currentTarget.style.color = "var(--text-disabled)"}
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>

        {/* Repo */}
        <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)", marginBottom: 10, display: "flex", alignItems: "center", gap: 5 }}>
          <GitBranch size={11} style={{ color: "var(--text-disabled)", flexShrink: 0 }} />
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {m.repo_url.replace("https://github.com/", "")}
          </span>
        </div>

        {/* Cloud migration visual — Brainboard-inspired */}
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "8px 10px", borderRadius: 8, background: "var(--surface-1)",
          border: "1px solid var(--surface-3)", marginBottom: 10,
        }}>
          <CloudBadge provider={m.source_cloud} />
          <div style={{ flex: 1, height: 1, background: "linear-gradient(90deg, var(--surface-4), var(--brand-500), var(--surface-4))" }} />
          <ArrowRight size={11} style={{ color: "var(--brand-500)", flexShrink: 0 }} />
          <div style={{ flex: 1, height: 1, background: "linear-gradient(90deg, var(--surface-4), var(--brand-500), var(--surface-4))" }} />
          <CloudBadge provider={m.target_cloud} />
        </div>

        {/* Tags */}
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {m.target_region && (
            <span style={{ padding: "2px 7px", borderRadius: 6, fontSize: 9.5, background: "var(--surface-2)", color: "var(--text-tertiary)", border: "1px solid var(--surface-4)" }}>
              📍 {m.target_region}
            </span>
          )}
          {m.seven_r_strategy && (
            <span style={{ padding: "2px 7px", borderRadius: 6, fontSize: 9.5, fontWeight: 600, background: "rgba(14,165,233,0.08)", color: "#0284c7", border: "1px solid rgba(14,165,233,0.2)" }}>
              {m.seven_r_strategy}
            </span>
          )}
          {isCompleted && (
            <span style={{ padding: "2px 7px", borderRadius: 6, fontSize: 9.5, fontWeight: 600, background: "rgba(22,163,74,0.08)", color: "#15803d", border: "1px solid rgba(22,163,74,0.2)" }}>
              IaC ✓
            </span>
          )}
        </div>

        {/* Active phase — Spacelift run status style */}
        {isActive && phase && (
          <div style={{
            marginTop: 10, padding: "6px 10px", borderRadius: 8,
            background: `${phase.color}10`, border: `1px solid ${phase.color}25`,
            display: "flex", alignItems: "center", gap: 6, fontSize: 11,
          }}>
            <span style={{ animation: "spin 2s linear infinite", display: "inline-block" }}>⚙</span>
            <span style={{ fontWeight: 600, color: phase.color }}>{phase.label}</span>
            <span style={{ color: "var(--text-tertiary)" }}>· {progress}%</span>
          </div>
        )}

        {/* Alerts */}
        {isPending && !isCompleted && !isActive && (
          <div style={{ marginTop: 8, padding: "5px 8px", borderRadius: 6, background: "var(--warning-subtle)", border: "1px solid var(--warning-border)", fontSize: 11, color: "var(--warning-text)", display: "flex", alignItems: "center", gap: 5 }}>
            <Clock size={11} /> {t("dashboard.action.required")}
          </div>
        )}
        {isFailed && (
          <div style={{ marginTop: 8, padding: "5px 8px", borderRadius: 6, background: "var(--error-subtle)", border: "1px solid var(--error-border)", fontSize: 11, color: "var(--error-text)", display: "flex", alignItems: "center", gap: 5 }}>
            <AlertCircle size={11} /> {t("dashboard.pipeline.failed")}
          </div>
        )}
      </div>

      {/* Footer */}
      <div style={{
        padding: "8px 16px", borderTop: "1px solid var(--surface-3)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        background: "var(--surface-1)",
      }}>
        <span style={{ fontSize: 10, fontFamily: "monospace", color: "var(--text-disabled)" }}>#{m.id.slice(0, 8)}</span>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{timeAgo} ago</span>
      </div>
    </motion.div>
  );
}

/* ── Table row — Spacelift run table style ────────────────────────────────── */
function MigrationRow({ m, onDelete, onClick }) {
  const { t } = useI18n();
  const isActive    = ACTIVE_STATUSES.has(m.status);
  const isCompleted = ["Completed","Exported"].includes(m.status);
  const isFailed    = ["Failed","Analysis_Failed"].includes(m.status);
  const progress    = STATUS_PROGRESS[m.status] ?? null;

  const now = new Date();
  const diffH = Math.round((now - new Date(m.created_at)) / 3600000);
  const timeAgo = diffH < 1 ? t("dashboard.instant") : diffH < 24 ? `${diffH}h` : `${Math.round(diffH/24)}j`;

  return (
    <motion.tr
      layout
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClick}
      style={{ cursor: "pointer", borderBottom: "1px solid var(--surface-3)" }}
      onMouseEnter={e => e.currentTarget.style.background = "var(--surface-1)"}
      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
    >
      <td style={{ padding: "10px 16px", width: 140 }}>
        <StatusBadge status={m.status} />
      </td>
      <td style={{ padding: "10px 8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <GitBranch size={12} style={{ color: "var(--text-disabled)", flexShrink: 0 }} />
          <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)" }}>
            {m.repo_url.replace("https://github.com/","").split("/").pop()}
          </span>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            {m.repo_url.replace("https://github.com/","").split("/")[0]}
          </span>
        </div>
      </td>
      <td style={{ padding: "10px 8px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <CloudBadge provider={m.source_cloud} />
          <ArrowRight size={10} style={{ color: "var(--text-disabled)" }} />
          <CloudBadge provider={m.target_cloud} />
        </div>
      </td>
      <td style={{ padding: "10px 8px" }}>
        {m.seven_r_strategy ? (
          <span style={{ padding: "2px 8px", borderRadius: 6, fontSize: 10.5, fontWeight: 600, background: "rgba(14,165,233,0.08)", color: "#0284c7", border: "1px solid rgba(14,165,233,0.2)" }}>
            {m.seven_r_strategy}
          </span>
        ) : <span style={{ color: "var(--text-disabled)", fontSize: 11 }}>—</span>}
      </td>
      <td style={{ padding: "10px 8px", width: 160 }}>
        {isActive && progress != null ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ flex: 1, height: 4, background: "var(--surface-3)", borderRadius: 2, overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${progress}%`, background: "linear-gradient(90deg,#6366f1,#0ea5e9)", borderRadius: 2, transition: "width 1s ease" }} />
            </div>
            <span style={{ fontSize: 10, color: "var(--text-tertiary)", flexShrink: 0 }}>{progress}%</span>
          </div>
        ) : isCompleted ? (
          <span style={{ fontSize: 11, color: "#16a34a", fontWeight: 600 }}>{t("dashboard.completed")}</span>
        ) : isFailed ? (
          <span style={{ fontSize: 11, color: "#dc2626", fontWeight: 600 }}>{t("dashboard.failed.row")}</span>
        ) : <span style={{ fontSize: 11, color: "var(--text-disabled)" }}>—</span>}
      </td>
      <td style={{ padding: "10px 8px", width: 80 }}>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{timeAgo}</span>
      </td>
      <td style={{ padding: "10px 16px", width: 60, textAlign: "right" }}>
        <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
          <button
            style={{ background: "none", border: "none", cursor: "pointer", padding: 4, borderRadius: 5, color: "var(--brand-500)" }}
            onClick={e => { e.stopPropagation(); onClick(); }}
          >
            <ChevronRight size={14} />
          </button>
          <button
            style={{ background: "none", border: "none", cursor: "pointer", padding: 4, borderRadius: 5, color: "var(--text-disabled)" }}
            onClick={e => { e.stopPropagation(); onDelete(); }}
            onMouseEnter={e => e.currentTarget.style.color = "#dc2626"}
            onMouseLeave={e => e.currentTarget.style.color = "var(--text-disabled)"}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </td>
    </motion.tr>
  );
}

/* ── Empty state — compelling CTA ────────────────────────────────────────── */
function EmptyState() {
  const { t } = useI18n();
  return (
    <div style={{ padding: "56px 24px", textAlign: "center", maxWidth: 540, margin: "0 auto" }}>
      <div style={{
        width: 64, height: 64, borderRadius: 16, margin: "0 auto 20px",
        background: "linear-gradient(135deg, rgba(99,102,241,0.12), rgba(14,165,233,0.12))",
        border: "1px solid var(--surface-4)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <Rocket size={28} style={{ color: "var(--brand-500)" }} />
      </div>

      <h3 style={{ fontSize: 20, fontWeight: 800, color: "var(--text-primary)", marginBottom: 8 }}>
        {t("dashboard.empty.title")}
      </h3>
      <p style={{ fontSize: 13.5, color: "var(--text-secondary)", lineHeight: 1.7, marginBottom: 24 }}>
        {t("dashboard.empty.desc")}
      </p>

      {/* Feature pills */}
      <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap", marginBottom: 28 }}>
        {[
          { label: "7R Taxonomy", icon: <Layers size={11} />, color: "#0284C7" },
          { label: "GraphRAG",    icon: <Zap size={11} />,    color: "#7C3AED" },
          { label: "Terraform IaC", icon: <Terminal size={11} />, color: "#16A34A" },
          { label: "Checkov Security", icon: <Shield size={11} />, color: "#DC2626" },
          { label: "Cost Estimate", icon: <DollarSign size={11} />, color: "#D97706" },
        ].map(t => (
          <span key={t.label} style={{
            padding: "5px 12px", borderRadius: 20, fontSize: 11.5, fontWeight: 600,
            background: `${t.color}10`, color: t.color, border: `1px solid ${t.color}25`,
            display: "flex", alignItems: "center", gap: 5,
          }}>
            {t.icon} {t.label}
          </span>
        ))}
      </div>

      <Link to="/migrations/new" style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "11px 24px", borderRadius: 10, textDecoration: "none",
        background: "linear-gradient(135deg, #6366f1, #0ea5e9)", color: "#fff",
        fontWeight: 700, fontSize: 14, boxShadow: "0 4px 14px rgba(99,102,241,0.35)",
      }}>
        <PlusCircle size={16} /> {t("dashboard.empty.launch")}
      </Link>
    </div>
  );
}

/* ── Main Dashboard ──────────────────────────────────────────────────────── */
export default function Dashboard() {
  const { t } = useI18n();
  const [migrations, setMigrations] = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);
  const [search, setSearch]         = useState("");
  const [filter, setFilter]         = useState("");
  const [viewMode, setViewMode]     = useState("grid"); // "grid" | "list"
  const navigate = useNavigate();

  const FILTER_OPTIONS = [
    { label: t("dashboard.filter.all"),       value: "" },
    { label: t("dashboard.filter.running"),   value: "running" },
    { label: t("dashboard.filter.completed"), value: "completed" },
    { label: t("dashboard.filter.pending"),   value: "pending" },
    { label: t("dashboard.filter.failed"),    value: "failed" },
  ];

  const load = () => {
    setLoading(true); setError(null);
    fetchMigrations()
      .then(list => { setMigrations(list); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  };
  useEffect(load, []);

  const running = migrations.filter(m => ACTIVE_STATUSES.has(m.status)).length;

  const filtered = migrations.filter(m => {
    const s = m.repo_url.toLowerCase() + m.id.toLowerCase();
    const matchSearch = !search || s.includes(search.toLowerCase());
    const matchFilter = !filter || (
      filter === "running"   ? ACTIVE_STATUSES.has(m.status) :
      filter === "completed" ? ["Completed","Exported"].includes(m.status) :
      filter === "failed"    ? ["Failed","Analysis_Failed"].includes(m.status) :
      filter === "pending"   ? ["Created","Plan_Ready","IaC_Ready","Accepted","Reviewing"].includes(m.status) : true
    );
    return matchSearch && matchFilter;
  });

  const handleDelete = async (id) => {
    if (!confirm("Supprimer définitivement cette migration ?")) return;
    await deleteMigration(id).catch(() => {});
    load();
  };

  return (
    <motion.div className="page" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.18 }}>

      {/* Header */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div>
          <h2 className="page-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {t("dashboard.title")}
            {running > 0 && (
              <span style={{
                padding: "2px 10px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                background: "rgba(99,102,241,0.1)", color: "#6366f1",
                border: "1px solid rgba(99,102,241,0.25)",
                animation: "pulse-dot 2s ease-in-out infinite",
              }}>
                {t("dashboard.active.badge", { n: running, s: running > 1 ? "s" : "" })}
              </span>
            )}
          </h2>
          <p className="page-subtitle">
            {loading ? t("dashboard.loading") : t("dashboard.count", { n: migrations.length, s: migrations.length !== 1 ? "s" : "" })}
          </p>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
          </button>
          <Link to="/migrations/new" style={{
            display: "inline-flex", alignItems: "center", gap: 7,
            padding: "8px 18px", borderRadius: 9, textDecoration: "none",
            background: "linear-gradient(135deg, #6366f1, #0ea5e9)", color: "#fff",
            fontWeight: 700, fontSize: 13,
            boxShadow: "0 2px 10px rgba(99,102,241,0.3)",
          }}>
            <PlusCircle size={14} /> {t("dashboard.new.btn")}
          </Link>
        </div>
      </div>

      {error && (
        <div style={{ marginBottom: 16, padding: "10px 14px", background: "var(--error-subtle)", border: "1px solid var(--error-border)", borderRadius: 8, fontSize: 12, color: "var(--error-text)" }}>
          {error}
        </div>
      )}

      {!loading && <AIBanner running={running} />}

      {/* Metrics row */}
      {loading ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 12, marginBottom: 24 }}>
          {[...Array(5)].map((_, i) => (
            <div key={i} style={{ height: 100, borderRadius: 12, background: "var(--surface-0)", border: "1px solid var(--surface-4)", animation: "skeleton-pulse 1.5s ease-in-out infinite" }} />
          ))}
        </div>
      ) : (
        <MetricsRow migrations={migrations} />
      )}

      {/* Search + Filter + View toggle */}
      {(migrations.length > 0 || loading) && (
        <div style={{ display: "flex", gap: 10, marginBottom: 18, alignItems: "center" }}>
          {/* Search */}
          <div style={{
            flex: 1, display: "flex", alignItems: "center", gap: 8,
            padding: "8px 12px", background: "var(--surface-0)",
            border: "1px solid var(--surface-4)", borderRadius: 9,
          }}>
            <Search size={14} style={{ color: "var(--text-disabled)", flexShrink: 0 }} />
            <input
              placeholder={t("dashboard.search.placeholder")}
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ border: "none", outline: "none", background: "transparent", fontSize: 13, color: "var(--text-primary)", width: "100%" }}
            />
          </div>

          {/* Filters */}
          <div style={{ display: "flex", gap: 4 }}>
            {FILTER_OPTIONS.map(opt => (
              <button key={opt.value} onClick={() => setFilter(opt.value)} style={{
                padding: "7px 13px", borderRadius: 8, fontSize: 12, cursor: "pointer",
                border: "1px solid var(--surface-4)",
                background: filter === opt.value ? "#6366f1" : "var(--surface-0)",
                color: filter === opt.value ? "#fff" : "var(--text-secondary)",
                fontWeight: filter === opt.value ? 600 : 400, transition: "all 150ms ease",
              }}>
                {opt.label}
              </button>
            ))}
          </div>

          {/* View toggle — Spacelift style */}
          <div style={{ display: "flex", background: "var(--surface-2)", borderRadius: 8, padding: 3, border: "1px solid var(--surface-4)" }}>
            {[{ mode: "grid", icon: <LayoutGrid size={14} /> }, { mode: "list", icon: <List size={14} /> }].map(v => (
              <button key={v.mode} onClick={() => setViewMode(v.mode)} style={{
                padding: "5px 10px", borderRadius: 6, border: "none", cursor: "pointer",
                background: viewMode === v.mode ? "var(--surface-0)" : "transparent",
                color: viewMode === v.mode ? "var(--brand-500)" : "var(--text-tertiary)",
                boxShadow: viewMode === v.mode ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
                transition: "all 150ms ease",
              }}>
                {v.icon}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>
          {[...Array(6)].map((_, i) => (
            <div key={i} style={{ height: 200, borderRadius: 12, background: "var(--surface-0)", border: "1px solid var(--surface-4)", animation: "skeleton-pulse 1.5s ease-in-out infinite" }} />
          ))}
        </div>
      ) : filtered.length === 0 && migrations.length === 0 ? (
        <EmptyState />
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--text-tertiary)" }}>
          <Inbox size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
          <p style={{ fontSize: 14 }}>{t("dashboard.no.match")}</p>
        </div>
      ) : viewMode === "grid" ? (
        <AnimatePresence>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 14 }}>
            {filtered.map(m => (
              <MigrationCard
                key={m.id} m={m}
                onClick={() => navigate(`/migrations/${m.id}`)}
                onDelete={() => handleDelete(m.id)}
              />
            ))}
          </div>
        </AnimatePresence>
      ) : (
        /* Table view — Spacelift runs style */
        <div style={{ background: "var(--surface-0)", borderRadius: 12, border: "1px solid var(--surface-4)", overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ background: "var(--surface-1)", borderBottom: "1px solid var(--surface-3)" }}>
                {[t("dashboard.table.status"),t("dashboard.table.repo"),t("dashboard.table.migration"),t("dashboard.table.strategy"),t("dashboard.table.progress"),t("dashboard.table.created"),""].map(h => (
                  <th key={h} style={{ padding: "10px 16px", textAlign: "left", fontSize: 10.5, fontWeight: 700, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <AnimatePresence>
              <tbody>
                {filtered.map(m => (
                  <MigrationRow
                    key={m.id} m={m}
                    onClick={() => navigate(`/migrations/${m.id}`)}
                    onDelete={() => handleDelete(m.id)}
                  />
                ))}
              </tbody>
            </AnimatePresence>
          </table>
        </div>
      )}
    </motion.div>
  );
}
