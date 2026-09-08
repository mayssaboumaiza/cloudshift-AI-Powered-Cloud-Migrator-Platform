import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import {
  Activity, CheckCircle, AlertCircle, Clock, Zap, RefreshCw,
  GitBranch, Filter, Download, ArrowRight, Cpu, Shield, Rocket,
  User, FileCode2, Search, ExternalLink,
} from "lucide-react";
import { fetchMigrations } from "../api/migrationApi";
import { useI18n } from "../context/I18nContext";

function timeAgo(dateStr, t) {
  const diff = (Date.now() - new Date(dateStr)) / 1000;
  if (diff < 60)    return t("activity.ago.sec",  { n: Math.round(diff) });
  if (diff < 3600)  return t("activity.ago.min",  { n: Math.round(diff / 60) });
  if (diff < 86400) return t("activity.ago.hour", { n: Math.round(diff / 3600) });
  return t("activity.ago.day", { n: Math.round(diff / 86400) });
}

function buildEvents(migrations) {
  const events = [];
  for (const m of migrations) {
    const repoName = m.repo_url.replace("https://github.com/", "");
    events.push({ id: `${m.id}-created`, migId: m.id, repoName, source: m.source_cloud, target: m.target_cloud, status: "Created", ts: m.created_at });
    if (m.status !== "Created") {
      events.push({ id: `${m.id}-${m.status}`, migId: m.id, repoName, source: m.source_cloud, target: m.target_cloud, status: m.status, ts: m.updated_at || m.created_at });
    }
  }
  return events.sort((a, b) => new Date(b.ts) - new Date(a.ts));
}

function filterEvent(ev, filter) {
  if (!filter) return true;
  const s = ev.status;
  if (filter === "success")  return ["Completed","Exported","Plan_Ready","IaC_Ready","Accepted"].includes(s);
  if (filter === "error")    return ["Failed","Analysis_Failed"].includes(s);
  if (filter === "running")  return ["Analyzing","Generating_IaC","Deploying","Health_Checking","Correcting","Validating_Intent"].includes(s);
  if (filter === "plan")     return ["Plan_Ready","Reviewing","Accepted","Correcting"].includes(s);
  return true;
}

const STATUS_CONFIG = {
  Created:           { icon: GitBranch,  color: "var(--info-text)",     bg: "var(--info-subtle)",     border: "var(--info-border)"     },
  Analyzing:         { icon: Cpu,        color: "var(--brand-500)",     bg: "var(--brand-50)",        border: "var(--brand-200)"       },
  Plan_Ready:        { icon: CheckCircle,color: "var(--success-text)",  bg: "var(--success-subtle)",  border: "var(--success-border)"  },
  Reviewing:         { icon: User,       color: "var(--warning-text)",  bg: "var(--warning-subtle)",  border: "var(--warning-border)"  },
  Accepted:          { icon: CheckCircle,color: "var(--success-text)",  bg: "var(--success-subtle)",  border: "var(--success-border)"  },
  Generating_IaC:    { icon: FileCode2,  color: "var(--ai-purple)",     bg: "var(--ai-purple-subtle)",border: "var(--ai-purple-border)"},
  Validating_Intent: { icon: Shield,     color: "var(--warning-text)",  bg: "var(--warning-subtle)",  border: "var(--warning-border)"  },
  IaC_Ready:         { icon: FileCode2,  color: "var(--success-text)",  bg: "var(--success-subtle)",  border: "var(--success-border)"  },
  Deploying:         { icon: Rocket,     color: "var(--ai-cyan)",       bg: "var(--ai-cyan-subtle)",  border: "var(--ai-cyan,#06B6D4)" },
  Health_Checking:   { icon: Activity,   color: "var(--info-text)",     bg: "var(--info-subtle)",     border: "var(--info-border)"     },
  Completed:         { icon: CheckCircle,color: "var(--success-text)",  bg: "var(--success-subtle)",  border: "var(--success-border)"  },
  Exported:          { icon: Download,   color: "var(--success-text)",  bg: "var(--success-subtle)",  border: "var(--success-border)"  },
  Failed:            { icon: AlertCircle,color: "var(--error-text)",    bg: "var(--error-subtle)",    border: "var(--error-border)"    },
  Analysis_Failed:   { icon: AlertCircle,color: "var(--error-text)",    bg: "var(--error-subtle)",    border: "var(--error-border)"    },
  Correcting:        { icon: RefreshCw,  color: "var(--warning-text)",  bg: "var(--warning-subtle)",  border: "var(--warning-border)"  },
};

function StatsRow({ events, t }) {
  const total     = events.length;
  const successes = events.filter(e => ["Completed","Exported"].includes(e.status)).length;
  const failures  = events.filter(e => ["Failed","Analysis_Failed"].includes(e.status)).length;
  const plans     = events.filter(e => e.status === "Plan_Ready").length;

  const stats = [
    { labelKey: "activity.filter.all",     value: total,     icon: Activity,    accent: "blue"   },
    { labelKey: "activity.filter.success",  value: successes, icon: CheckCircle, accent: "green"  },
    { labelKey: "activity.filter.error",    value: failures,  icon: AlertCircle, accent: "red"    },
    { labelKey: "activity.filter.plan",     value: plans,     icon: FileCode2,   accent: "purple" },
  ];

  return (
    <div className="stats-bar" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 18 }}>
      {stats.map((s, i) => (
        <motion.div
          key={s.labelKey}
          className="stat-card"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.05, duration: 0.2 }}
        >
          <div className={`stat-icon ${s.accent}`}><s.icon size={18} strokeWidth={1.8} /></div>
          <div className="stat-info">
            <div className="stat-value">{s.value}</div>
            <div className="stat-label">{t(s.labelKey)}</div>
          </div>
        </motion.div>
      ))}
    </div>
  );
}

export default function ActivityLog() {
  const { t } = useI18n();
  const [migrations, setMigrations] = useState([]);
  const [loading, setLoading]       = useState(true);
  const [filter, setFilter]         = useState("");
  const [search, setSearch]         = useState("");
  const [error, setError]           = useState(null);

  const load = () => {
    setLoading(true);
    fetchMigrations()
      .then(setMigrations)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const FILTER_TYPES = [
    { labelKey: "activity.filter.all",     value: "" },
    { labelKey: "activity.filter.success", value: "success" },
    { labelKey: "activity.filter.error",   value: "error" },
    { labelKey: "activity.filter.running", value: "running" },
    { labelKey: "activity.filter.plan",    value: "plan" },
  ];

  const allEvents = buildEvents(migrations);
  const filtered  = allEvents.filter(ev => {
    const matchFilter = filterEvent(ev, filter);
    const matchSearch = !search ||
      ev.repoName.toLowerCase().includes(search.toLowerCase()) ||
      ev.migId.toLowerCase().includes(search.toLowerCase()) ||
      ev.status.toLowerCase().includes(search.toLowerCase());
    return matchFilter && matchSearch;
  });

  return (
    <motion.div className="page" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.18 }}>
      <div className="page-header">
        <div>
          <h2 className="page-title">{t("activity.title")}</h2>
          <p className="page-subtitle">
            {loading ? t("activity.loading") : `${allEvents.length} ${t("activity.subtitle")} — ${migrations.length} migrations`}
          </p>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
            {t("common.refresh")}
          </button>
        </div>
      </div>

      {!loading && <StatsRow events={allEvents} t={t} />}

      {/* Toolbar */}
      <div style={{ display: "flex", gap: 10, marginBottom: 20, alignItems: "center" }}>
        <div className="search-bar" style={{ flex: 1, marginBottom: 0 }}>
          <Search size={14} />
          <input
            placeholder={t("activity.search")}
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
          <Filter size={13} style={{ color: "var(--text-tertiary)" }} />
          {FILTER_TYPES.map(f => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              style={{
                padding: "5px 12px", borderRadius: 20, fontSize: 11.5,
                cursor: "pointer", border: "1px solid var(--surface-4)",
                background: filter === f.value ? "var(--brand-500)" : "var(--surface-0)",
                color:      filter === f.value ? "#fff"             : "var(--text-secondary)",
                fontWeight: filter === f.value ? 600 : 400,
                transition: "all 150ms",
              }}
            >
              {t(f.labelKey)}
            </button>
          ))}
        </div>
      </div>

      {/* Events timeline */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {[...Array(8)].map((_, i) => (
            <div key={i} style={{
              height: 64, borderRadius: 10, background: "var(--surface-3)",
              animation: "skeleton-pulse 1.5s ease-in-out infinite",
              animationDelay: `${i * 0.07}s`,
            }} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: "center", padding: "48px 24px", color: "var(--text-tertiary)", fontSize: 14 }}>
          <Activity size={32} style={{ marginBottom: 12, opacity: 0.3 }} />
          <div>{t("activity.empty")}</div>
          <div style={{ fontSize: 12, marginTop: 6, opacity: 0.7 }}>{t("activity.empty.sub")}</div>
        </div>
      ) : (
        <div style={{ position: "relative" }}>
          <div style={{
            position: "absolute", left: 21, top: 12, bottom: 12,
            width: 2, background: "var(--surface-4)", borderRadius: 2, zIndex: 0,
          }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {filtered.map((ev, i) => {
              const cfg  = STATUS_CONFIG[ev.status] || STATUS_CONFIG.Created;
              const Icon = cfg.icon;
              const label = t(`event.${ev.status}`) || ev.status;
              return (
                <motion.div
                  key={ev.id}
                  initial={{ opacity: 0, x: -12 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.03, duration: 0.2 }}
                  style={{ display: "flex", alignItems: "flex-start", gap: 12, position: "relative", zIndex: 1 }}
                >
                  <div style={{
                    width: 32, height: 32, borderRadius: "50%",
                    background: "var(--surface-0)",
                    border: `2px solid ${cfg.border}`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    flexShrink: 0, zIndex: 1,
                  }}>
                    <Icon size={14} style={{ color: cfg.color }} />
                  </div>

                  <div style={{
                    flex: 1, padding: "10px 14px", borderRadius: 9,
                    background: "var(--surface-0)",
                    border: `1px solid ${cfg.border}`,
                    display: "flex", alignItems: "center", gap: 10,
                    minWidth: 0, transition: "box-shadow 150ms",
                  }}>
                    <span style={{
                      padding: "2px 9px", borderRadius: 20, fontSize: 11, fontWeight: 700,
                      background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
                      flexShrink: 0, whiteSpace: "nowrap",
                    }}>
                      {label}
                    </span>

                    <span style={{
                      fontSize: 12.5, color: "var(--text-secondary)",
                      fontFamily: "var(--font-mono)", flex: 1, minWidth: 0,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {ev.repoName}
                    </span>

                    <span style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0 }}>
                      <span className={`cloud-label-inline ${ev.source?.toLowerCase()}`} style={{ fontSize: 9.5, padding: "2px 6px" }}>
                        {ev.source?.toUpperCase()}
                      </span>
                      <ArrowRight size={11} style={{ color: "var(--text-tertiary)" }} />
                      <span className={`cloud-label-inline ${ev.target?.toLowerCase()}`} style={{ fontSize: 9.5, padding: "2px 6px" }}>
                        {ev.target?.toUpperCase()}
                      </span>
                    </span>

                    <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                      <Link
                        to={`/migrations/${ev.migId}`}
                        onClick={e => e.stopPropagation()}
                        style={{
                          display: "flex", alignItems: "center", gap: 4,
                          fontSize: 11, fontFamily: "var(--font-mono)", fontWeight: 600,
                          color: "var(--brand-600)", textDecoration: "none",
                          padding: "2px 7px", background: "var(--brand-50)",
                          borderRadius: 5, border: "1px solid var(--brand-200)",
                          transition: "background 120ms",
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = "var(--brand-100)"}
                        onMouseLeave={e => e.currentTarget.style.background = "var(--brand-50)"}
                      >
                        #{ev.migId.slice(0, 8)}
                        <ExternalLink size={9} />
                      </Link>
                      <span style={{ fontSize: 10.5, color: "var(--text-disabled)", fontFamily: "var(--font-mono)" }}>
                        {timeAgo(ev.ts, t)}
                      </span>
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </div>
        </div>
      )}
    </motion.div>
  );
}
