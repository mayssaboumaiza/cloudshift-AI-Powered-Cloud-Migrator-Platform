import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import {
  Network, RefreshCw, ArrowRight, GitBranch, ExternalLink,
  Cloud, CheckCircle, AlertCircle, Activity, Clock,
} from "lucide-react";
import { fetchMigrations } from "../api/migrationApi";
import StatusBadge from "../components/common/StatusBadge";

const CLOUD_COLORS = {
  aws:   { color: "#FF9900", bg: "#FFF7ED", border: "#FED7AA", label: "AWS"   },
  azure: { color: "#0078D4", bg: "#F0F9FF", border: "#BAE6FD", label: "Azure" },
  gcp:   { color: "#4285F4", bg: "#EFF6FF", border: "#BFDBFE", label: "GCP"   },
};

const ACTIVE_STATUSES = new Set([
  "Analyzing", "Generating_IaC", "Validating_Intent", "Deploying", "Health_Checking", "Correcting",
]);

function CloudNode({ provider, count, label }) {
  const c = CLOUD_COLORS[provider?.toLowerCase()] || { color: "#6366F1", bg: "#F5F3FF", border: "#DDD6FE", label: provider || "?" };
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", gap: 6,
      padding: "14px 20px", borderRadius: 12,
      background: c.bg, border: `2px solid ${c.border}`,
      minWidth: 90,
    }}>
      <div style={{
        width: 40, height: 40, borderRadius: 10,
        background: `${c.color}18`,
        border: `1.5px solid ${c.border}`,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <Cloud size={20} style={{ color: c.color }} />
      </div>
      <span style={{ fontWeight: 800, fontSize: 13, color: c.color }}>{c.label}</span>
      {count !== undefined && (
        <span style={{
          fontSize: 10, fontWeight: 600, color: "var(--text-tertiary)",
          background: "var(--surface-3)", padding: "1px 7px", borderRadius: 10,
        }}>
          {count} migration{count !== 1 ? "s" : ""}
        </span>
      )}
      {label && <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{label}</span>}
    </div>
  );
}

function RouteCard({ source, target, migrations }) {
  const [expanded, setExpanded] = useState(false);
  const sc = CLOUD_COLORS[source?.toLowerCase()] || {};
  const tc = CLOUD_COLORS[target?.toLowerCase()] || {};
  const completed = migrations.filter(m => ["Completed", "Exported"].includes(m.status)).length;
  const active    = migrations.filter(m => ACTIVE_STATUSES.has(m.status)).length;
  const failed    = migrations.filter(m => ["Failed", "Analysis_Failed"].includes(m.status)).length;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      style={{
        borderRadius: 12, border: "1px solid var(--surface-4)",
        background: "var(--surface-0)", overflow: "hidden",
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <div
        style={{
          display: "flex", alignItems: "center", gap: 14,
          padding: "14px 16px", cursor: "pointer",
        }}
        onClick={() => setExpanded(v => !v)}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{
            padding: "4px 10px", borderRadius: 7, fontSize: 12, fontWeight: 700,
            color: sc.color, background: sc.bg, border: `1px solid ${sc.border}`,
          }}>{(sc.label || source)?.toUpperCase()}</span>
          <ArrowRight size={16} style={{ color: "var(--text-tertiary)" }} />
          <span style={{
            padding: "4px 10px", borderRadius: 7, fontSize: 12, fontWeight: 700,
            color: tc.color, background: tc.bg, border: `1px solid ${tc.border}`,
          }}>{(tc.label || target)?.toUpperCase()}</span>
        </div>

        <div style={{ flex: 1 }} />

        <div style={{ display: "flex", gap: 10, fontSize: 11 }}>
          <span style={{ color: "#16A34A", fontWeight: 600 }}>✓ {completed}</span>
          {active > 0 && <span style={{ color: "var(--brand-500)", fontWeight: 600 }}>↻ {active}</span>}
          {failed > 0 && <span style={{ color: "var(--error-solid)", fontWeight: 600 }}>✗ {failed}</span>}
          <span style={{
            padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 700,
            background: "var(--surface-3)", color: "var(--text-secondary)",
          }}>
            {migrations.length} total
          </span>
        </div>

        <span style={{ fontSize: 11, color: "var(--text-tertiary)", marginLeft: 4 }}>
          {expanded ? "▲" : "▼"}
        </span>
      </div>

      {expanded && (
        <div style={{ borderTop: "1px solid var(--surface-3)", padding: "10px 16px 14px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {migrations.map(m => (
              <Link
                key={m.id}
                to={`/migrations/${m.id}`}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 12px", borderRadius: 8,
                  background: "var(--surface-1)", border: "1px solid var(--surface-4)",
                  textDecoration: "none", transition: "background 120ms",
                }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--surface-3)"}
                onMouseLeave={e => e.currentTarget.style.background = "var(--surface-1)"}
              >
                <GitBranch size={12} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
                <span style={{
                  fontSize: 12, color: "var(--text-secondary)",
                  fontFamily: "var(--font-mono)", flex: 1, minWidth: 0,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {m.repo_url.replace("https://github.com/", "")}
                </span>
                <StatusBadge status={m.status} />
                {m.seven_r_strategy && (
                  <span style={{
                    fontSize: 9.5, padding: "1px 6px", borderRadius: 8, fontWeight: 600,
                    background: "var(--info-subtle)", color: "var(--info-text)", border: "1px solid var(--info-border)",
                  }}>
                    {m.seven_r_strategy}
                  </span>
                )}
                <span style={{
                  fontSize: 10, fontFamily: "var(--font-mono)",
                  color: "var(--brand-600)", flexShrink: 0,
                }}>
                  #{m.id.slice(0, 8)}
                </span>
                <ExternalLink size={11} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
              </Link>
            ))}
          </div>
        </div>
      )}
    </motion.div>
  );
}

export default function InfrastructureGraph() {
  const [migrations, setMigrations] = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);

  const load = () => {
    setLoading(true);
    fetchMigrations()
      .then(setMigrations)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  /* Group migrations by source→target route */
  const routeMap = {};
  for (const m of migrations) {
    const key = `${m.source_cloud}→${m.target_cloud}`;
    if (!routeMap[key]) routeMap[key] = { source: m.source_cloud, target: m.target_cloud, migrations: [] };
    routeMap[key].migrations.push(m);
  }
  const routes = Object.values(routeMap).sort((a, b) => b.migrations.length - a.migrations.length);

  /* Cloud usage counts */
  const cloudUsage = {};
  for (const m of migrations) {
    cloudUsage[m.target_cloud] = (cloudUsage[m.target_cloud] || 0) + 1;
  }

  const total     = migrations.length;
  const active    = migrations.filter(m => ACTIVE_STATUSES.has(m.status)).length;
  const completed = migrations.filter(m => ["Completed", "Exported"].includes(m.status)).length;

  return (
    <motion.div
      className="page"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
    >
      <div className="page-header">
        <div>
          <h2 className="page-title">Infrastructure Graph</h2>
          <p className="page-subtitle">
            {loading ? "Chargement…" : `${total} migration${total !== 1 ? "s" : ""} · ${routes.length} route${routes.length !== 1 ? "s" : ""} cloud`}
          </p>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
            Rafraîchir
          </button>
          <Link to="/migrations/new" className="btn btn-primary">
            Nouvelle Migration
          </Link>
        </div>
      </div>

      {error && (
        <div style={{
          padding: "10px 14px", borderRadius: 8, marginBottom: 16,
          background: "var(--error-subtle)", border: "1px solid var(--error-border)",
          color: "var(--error-text)", fontSize: 13,
        }}>
          {error}
        </div>
      )}

      {/* Summary stats */}
      {!loading && (
        <div className="stats-bar" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 24 }}>
          {[
            { icon: Network,      label: "Routes cloud",    value: routes.length,  accent: "blue"   },
            { icon: Cloud,        label: "Migrations total", value: total,          accent: "blue"   },
            { icon: Activity,     label: "En cours",         value: active,         accent: "blue"   },
            { icon: CheckCircle,  label: "Complétées",       value: completed,      accent: "green"  },
          ].map((s, i) => (
            <motion.div
              key={s.label}
              className="stat-card"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05, duration: 0.2 }}
            >
              <div className={`stat-icon ${s.accent}`}><s.icon size={18} strokeWidth={1.8} /></div>
              <div className="stat-info">
                <div className="stat-value">{s.value}</div>
                <div className="stat-label">{s.label}</div>
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {[...Array(4)].map((_, i) => (
            <div key={i} style={{
              height: 60, borderRadius: 12, background: "var(--surface-3)",
              animation: "skeleton-pulse 1.5s ease-in-out infinite",
              animationDelay: `${i * 0.1}s`,
            }} />
          ))}
        </div>
      ) : routes.length === 0 ? (
        <div style={{ textAlign: "center", padding: "60px 24px" }}>
          <Network size={40} style={{ color: "var(--text-disabled)", marginBottom: 14, display: "block", margin: "0 auto 14px" }} />
          <p style={{ fontSize: 14, color: "var(--text-tertiary)", marginBottom: 16 }}>
            Aucune migration trouvée. Créez votre première migration pour visualiser l'infrastructure.
          </p>
          <Link to="/migrations/new" className="btn btn-primary">Nouvelle Migration</Link>
        </div>
      ) : (
        <>
          {/* Cloud topology overview */}
          {Object.keys(cloudUsage).length > 0 && (
            <div style={{
              padding: "16px 20px", borderRadius: 12, marginBottom: 24,
              background: "var(--surface-0)", border: "1px solid var(--surface-4)",
              boxShadow: "var(--shadow-sm)",
            }}>
              <div style={{
                fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                letterSpacing: "0.1em", color: "var(--text-disabled)", marginBottom: 14,
              }}>
                Plateformes cibles actives
              </div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                {Object.entries(cloudUsage).map(([cloud, count]) => (
                  <CloudNode key={cloud} provider={cloud} count={count} />
                ))}
              </div>
            </div>
          )}

          {/* Route cards */}
          <div style={{
            fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            letterSpacing: "0.1em", color: "var(--text-disabled)", marginBottom: 10,
          }}>
            Routes de migration ({routes.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {routes.map(r => (
              <RouteCard
                key={`${r.source}→${r.target}`}
                source={r.source}
                target={r.target}
                migrations={r.migrations}
              />
            ))}
          </div>
        </>
      )}
    </motion.div>
  );
}
