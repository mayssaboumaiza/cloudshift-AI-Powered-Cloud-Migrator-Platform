import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import {
  CheckCircle, XCircle, Clock, Zap,
  ArrowRight, Layers, Activity, BarChart2, ChevronRight,
  RefreshCw, Cpu, Cloud,
} from "lucide-react";
import { fetchMigrations } from "../api/migrationApi";
import { useI18n } from "../context/I18nContext";

const ACTIVE = new Set(["Analyzing","Generating_IaC","Validating_Intent","Deploying","Health_Checking","Correcting"]);
const DONE   = ["Completed","Exported"];
const FAILED = ["Failed","Analysis_Failed"];

const PROVIDER_COLORS = {
  aws:   { color: "#FF9900", bg: "#FFF7ED", label: "AWS" },
  gcp:   { color: "#4285F4", bg: "#EFF6FF", label: "GCP" },
  azure: { color: "#0078D4", bg: "#F0F9FF", label: "Azure" },
};
const STRATEGY_COLORS = {
  Rehost:"#0EA5E9", Replatform:"#6366F1", Refactor:"#7C3AED",
  Retire:"#16A34A", Retain:"#9CA3AF",    Repurchase:"#D97706", Relocate:"#06B6D4",
};

function pct(a, b) { return b > 0 ? Math.round((a / b) * 100) : 0; }

function Counter({ value, suffix = "" }) {
  const [display, setDisplay] = useState(0);
  useEffect(() => {
    let start = 0;
    const step = Math.ceil(value / 24);
    const id = setInterval(() => {
      start = Math.min(start + step, value);
      setDisplay(start);
      if (start >= value) clearInterval(id);
    }, 30);
    return () => clearInterval(id);
  }, [value]);
  return <>{display}{suffix}</>;
}

function KPICard({ icon, value, suffix = "", label, sub, color, bg, delay = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay, duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
      style={{
        padding: "22px 24px", borderRadius: 14,
        background: "var(--surface-0)", border: "1px solid var(--surface-4)",
        boxShadow: "0 1px 4px rgba(0,0,0,0.04)", position: "relative", overflow: "hidden",
      }}
    >
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: color, opacity: 0.8 }} />
      <div style={{ width: 44, height: 44, borderRadius: 12, background: bg, display: "flex", alignItems: "center", justifyContent: "center", color, marginBottom: 14 }}>
        {icon}
      </div>
      <div style={{ fontSize: 36, fontWeight: 900, color: "var(--text-primary)", lineHeight: 1, letterSpacing: "-0.02em" }}>
        <Counter value={value} suffix={suffix} />
      </div>
      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-secondary)", marginTop: 6 }}>{label}</div>
      {sub && <div style={{ fontSize: 11, color: "var(--text-disabled)", marginTop: 3 }}>{sub}</div>}
    </motion.div>
  );
}

function BarChart({ data, colorKey = "color", labelKey = "label", valueKey = "count" }) {
  const max = Math.max(...data.map(d => d[valueKey]), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {data.map((d, i) => (
        <motion.div key={d[labelKey]} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.1 + i * 0.05 }} style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 80, fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", textAlign: "right", flexShrink: 0 }}>{d[labelKey]}</div>
          <div style={{ flex: 1, height: 22, background: "var(--surface-2)", borderRadius: 6, overflow: "hidden" }}>
            <motion.div initial={{ width: 0 }} animate={{ width: `${pct(d[valueKey], max)}%` }} transition={{ delay: 0.2 + i * 0.06, duration: 0.5, ease: [0.16,1,0.3,1] }} style={{ height: "100%", background: d[colorKey], borderRadius: 6, minWidth: d[valueKey] > 0 ? 8 : 0 }} />
          </div>
          <div style={{ width: 28, fontSize: 12, fontWeight: 700, color: "var(--text-primary)", textAlign: "left", flexShrink: 0 }}>{d[valueKey]}</div>
        </motion.div>
      ))}
    </div>
  );
}

function DonutSegment({ pct: p, color, label, value }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
      <svg width={14} height={14}><circle cx={7} cy={7} r={6} fill={color} /></svg>
      <span style={{ fontSize: 12, color: "var(--text-secondary)", flex: 1 }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>{value}</span>
      <span style={{ fontSize: 11, color: "var(--text-disabled)", width: 36, textAlign: "right" }}>{p}%</span>
    </div>
  );
}

function RecentList({ migrations }) {
  const recent = [...migrations].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).slice(0, 6);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
      {recent.map((m, i) => {
        const isDone = DONE.includes(m.status), isFailed = FAILED.includes(m.status), isActive = ACTIVE.has(m.status);
        const dotColor = isDone ? "#16a34a" : isFailed ? "#dc2626" : isActive ? "#6366f1" : "#d97706";
        const diffH = Math.round((Date.now() - new Date(m.created_at)) / 3600000);
        const timeAgo = diffH < 1 ? "< 1h" : diffH < 24 ? `${diffH}h` : `${Math.round(diffH/24)}j`;
        return (
          <motion.div key={m.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.04 }}>
            <Link to={`/migrations/${m.id}`} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderRadius: 8, textDecoration: "none", transition: "background 150ms" }}
              onMouseEnter={e => e.currentTarget.style.background = "var(--surface-1)"}
              onMouseLeave={e => e.currentTarget.style.background = "transparent"}
            >
              <span style={{ width: 8, height: 8, borderRadius: "50%", background: dotColor, flexShrink: 0 }} />
              <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {m.repo_url.replace("https://github.com/","")}
              </span>
              <span style={{ fontSize: 10.5, color: "var(--text-tertiary)", flexShrink: 0 }}>
                {(PROVIDER_COLORS[m.source_cloud?.toLowerCase()] || {}).label || m.source_cloud}{" → "}{(PROVIDER_COLORS[m.target_cloud?.toLowerCase()] || {}).label || m.target_cloud}
              </span>
              <span style={{ fontSize: 10, color: "var(--text-disabled)", flexShrink: 0, width: 24, textAlign: "right" }}>{timeAgo}</span>
              <ChevronRight size={12} style={{ color: "var(--text-disabled)", flexShrink: 0 }} />
            </Link>
          </motion.div>
        );
      })}
    </div>
  );
}

export default function Analytics() {
  const { t } = useI18n();
  const [migrations, setMigrations] = useState([]);
  const [loading, setLoading]       = useState(true);

  const reload = () => {
    setLoading(true);
    fetchMigrations().then(l => { setMigrations(l); setLoading(false); }).catch(() => setLoading(false));
  };
  useEffect(reload, []);

  const total       = migrations.length;
  const running     = migrations.filter(m => ACTIVE.has(m.status)).length;
  const completed   = migrations.filter(m => DONE.includes(m.status)).length;
  const failed      = migrations.filter(m => FAILED.includes(m.status)).length;
  const successRate = pct(completed, total);

  const providerCounts = {};
  migrations.forEach(m => { const tgt = m.target_cloud?.toLowerCase(); if (tgt) providerCounts[tgt] = (providerCounts[tgt] || 0) + 1; });
  const providerData = Object.entries(providerCounts).map(([k, v]) => ({ label: PROVIDER_COLORS[k]?.label || k.toUpperCase(), count: v, color: PROVIDER_COLORS[k]?.color || "#6366f1", pct: pct(v, total) })).sort((a, b) => b.count - a.count);

  const strategyCounts = {};
  migrations.forEach(m => { if (m.seven_r_strategy) strategyCounts[m.seven_r_strategy] = (strategyCounts[m.seven_r_strategy] || 0) + 1; });
  const strategyData = Object.entries(strategyCounts).map(([k, v]) => ({ label: k, count: v, color: STRATEGY_COLORS[k] || "#6366f1" })).sort((a, b) => b.count - a.count);

  const pending = migrations.filter(m => ["Plan_Ready","Reviewing","Accepted"].includes(m.status)).length;

  if (loading) return (
    <div className="page">
      <div style={{ height: 32, width: 200, borderRadius: 8, background: "var(--surface-3)", animation: "skeleton-pulse 1.5s infinite", marginBottom: 24 }} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px,1fr))", gap: 14, marginBottom: 24 }}>
        {[...Array(4)].map((_, i) => <div key={i} style={{ height: 130, borderRadius: 14, background: "var(--surface-0)", border: "1px solid var(--surface-4)", animation: "skeleton-pulse 1.5s infinite" }} />)}
      </div>
    </div>
  );

  return (
    <motion.div className="page" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.2 }}>

      <div className="page-header" style={{ marginBottom: 28 }}>
        <div>
          <h2 className="page-title">{t("analytics.title")}</h2>
          <p className="page-subtitle">{t("analytics.sub", { n: total, s: total !== 1 ? "s" : "" })}</p>
        </div>
        <button className="btn btn-secondary" onClick={reload}>
          <RefreshCw size={14} /> {t("analytics.refresh")}
        </button>
      </div>

      {/* KPI row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px,1fr))", gap: 14, marginBottom: 28 }}>
        <KPICard icon={<Layers size={22} />}      value={total}       label={t("analytics.kpi.total")}    sub={t("analytics.kpi.total.sub")}                                    color="#6366F1" bg="rgba(99,102,241,0.08)"  delay={0}    />
        <KPICard icon={<CheckCircle size={22} />} value={successRate} suffix="%" label={t("analytics.kpi.rate")}  sub={`${completed} ${t("analytics.status.done").toLowerCase()}`}  color="#16A34A" bg="rgba(22,163,74,0.08)"  delay={0.06} />
        <KPICard icon={<Activity size={22} />}    value={running}     label={t("analytics.kpi.running")}  sub={running > 0 ? t("analytics.kpi.active") : t("analytics.kpi.none")} color="#0EA5E9" bg="rgba(14,165,233,0.08)" delay={0.12} />
        <KPICard icon={<XCircle size={22} />}     value={failed}      label={t("analytics.kpi.failures")} sub={failed > 0 ? t("analytics.kpi.investigate") : t("analytics.kpi.ok")} color={failed > 0 ? "#DC2626" : "#9CA3AF"} bg={failed > 0 ? "rgba(220,38,38,0.08)" : "rgba(156,163,175,0.06)"} delay={0.18} />
      </div>

      {/* Provider + Strategy */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }} style={{ padding: "20px 22px", borderRadius: 14, background: "var(--surface-0)", border: "1px solid var(--surface-4)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18 }}>
            <Cloud size={16} style={{ color: "var(--brand-500)" }} />
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{t("analytics.provider.title")}</span>
          </div>
          {providerData.length > 0 ? (
            <>
              <BarChart data={providerData} />
              <div style={{ borderTop: "1px solid var(--surface-3)", marginTop: 16, paddingTop: 14, display: "flex", flexDirection: "column", gap: 4 }}>
                {providerData.map(d => <DonutSegment key={d.label} pct={d.pct} color={d.color} label={d.label} value={d.count} />)}
              </div>
            </>
          ) : <p style={{ color: "var(--text-tertiary)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>{t("analytics.no.data")}</p>}
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} style={{ padding: "20px 22px", borderRadius: 14, background: "var(--surface-0)", border: "1px solid var(--surface-4)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18 }}>
            <BarChart2 size={16} style={{ color: "#7C3AED" }} />
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{t("analytics.strategy.title")}</span>
          </div>
          {strategyData.length > 0 ? <BarChart data={strategyData} /> : <p style={{ color: "var(--text-tertiary)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>{t("analytics.no.strategy")}</p>}
        </motion.div>
      </div>

      {/* Status breakdown + Recent */}
      <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", gap: 16 }}>
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.35 }} style={{ padding: "20px 22px", borderRadius: 14, background: "var(--surface-0)", border: "1px solid var(--surface-4)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18 }}>
            <Zap size={16} style={{ color: "#D97706" }} />
            <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{t("analytics.status.title")}</span>
          </div>
          {[
            { key: "analytics.status.done",    value: completed, color: "#16A34A" },
            { key: "analytics.status.running",  value: running,   color: "#6366F1" },
            { key: "analytics.status.pending",  value: pending,   color: "#D97706" },
            { key: "analytics.status.failed",   value: failed,    color: "#DC2626" },
          ].map(s => (
            <div key={s.key} style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)" }}>{t(s.key)}</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: s.color }}>{s.value} <span style={{ color: "var(--text-disabled)", fontWeight: 400 }}>({pct(s.value, total)}%)</span></span>
              </div>
              <div style={{ height: 6, background: "var(--surface-2)", borderRadius: 3, overflow: "hidden" }}>
                <motion.div initial={{ width: 0 }} animate={{ width: `${pct(s.value, total)}%` }} transition={{ delay: 0.4, duration: 0.6, ease: [0.16,1,0.3,1] }} style={{ height: "100%", background: s.color, borderRadius: 3 }} />
              </div>
            </div>
          ))}
          <div style={{ marginTop: 20, padding: "12px 14px", borderRadius: 10, background: successRate >= 70 ? "rgba(22,163,74,0.06)" : successRate >= 40 ? "rgba(217,119,6,0.06)" : "rgba(220,38,38,0.06)", border: `1px solid ${successRate >= 70 ? "rgba(22,163,74,0.2)" : successRate >= 40 ? "rgba(217,119,6,0.2)" : "rgba(220,38,38,0.2)"}` }}>
            <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--text-tertiary)", marginBottom: 4 }}>{t("analytics.pipeline.score")}</div>
            <div style={{ fontSize: 26, fontWeight: 900, color: successRate >= 70 ? "#16a34a" : successRate >= 40 ? "#d97706" : "#dc2626" }}>
              {successRate >= 70 ? "🟢" : successRate >= 40 ? "🟡" : "🔴"} {successRate}%
            </div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
              {successRate >= 70 ? t("analytics.score.excellent") : successRate >= 40 ? t("analytics.score.ok") : t("analytics.score.improve")}
            </div>
          </div>
        </motion.div>

        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 }} style={{ padding: "20px 22px", borderRadius: 14, background: "var(--surface-0)", border: "1px solid var(--surface-4)" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Clock size={16} style={{ color: "#0EA5E9" }} />
              <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{t("analytics.recent.title")}</span>
            </div>
            <Link to="/dashboard" style={{ fontSize: 11, color: "var(--brand-500)", textDecoration: "none", fontWeight: 600, display: "flex", alignItems: "center", gap: 3 }}>
              {t("analytics.see.all")} <ArrowRight size={11} />
            </Link>
          </div>
          {migrations.length > 0 ? <RecentList migrations={migrations} /> : (
            <p style={{ color: "var(--text-tertiary)", fontSize: 12, textAlign: "center", padding: "24px 0" }}>{t("analytics.no.migrations")}</p>
          )}
        </motion.div>
      </div>

      {/* AI Agents KPI */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.45 }} style={{ marginTop: 16, padding: "20px 22px", borderRadius: 14, background: "linear-gradient(135deg, rgba(99,102,241,0.04), rgba(14,165,233,0.04))", border: "1px solid rgba(99,102,241,0.15)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18 }}>
          <Cpu size={16} style={{ color: "#6366f1" }} />
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>{t("analytics.pipeline.title")}</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px,1fr))", gap: 14 }}>
          {[
            { agent: "Migration Planner",   sub: "7R · AHP Scoring · ReAct",            icon: "🎯", color: "#0EA5E9", runs: completed + running + failed },
            { agent: "IaC Generator",       sub: "GraphRAG · Terraform · Checkov",      icon: "⚙️", color: "#7C3AED", runs: migrations.filter(m => !["Created","Analyzing","Plan_Ready","Reviewing"].includes(m.status)).length },
            { agent: "Deploy Orchestrator", sub: "deploy.sh · CI/CD · TerraformRunner", icon: "🚀", color: "#16A34A", runs: completed },
          ].map(a => (
            <div key={a.agent} style={{ padding: "14px 16px", borderRadius: 10, background: "var(--surface-0)", border: "1px solid var(--surface-4)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <span style={{ fontSize: 20 }}>{a.icon}</span>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: a.color }}>{a.agent}</div>
                  <div style={{ fontSize: 10.5, color: "var(--text-secondary)" }}>{a.sub}</div>
                </div>
              </div>
              <div style={{ fontSize: 28, fontWeight: 900, color: "var(--text-primary)" }}>{a.runs}</div>
            </div>
          ))}
        </div>
      </motion.div>
    </motion.div>
  );
}
