import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Link } from "react-router-dom";
import {
  DollarSign, RefreshCw, TrendingDown, CheckCircle, AlertCircle,
  BarChart2, Cloud, ArrowRight, ExternalLink, Layers,
} from "lucide-react";
import { fetchMigrations } from "../api/migrationApi";
import StatusBadge from "../components/common/StatusBadge";

/* Estimated monthly savings per strategy (indicative values) */
const STRATEGY_SAVINGS = {
  Rehost:      { min: 15, max: 30,  label: "Rehost (Lift & Shift)",    color: "#0284C7" },
  Replatform:  { min: 25, max: 45,  label: "Replatform",               color: "#6366F1" },
  Refactor:    { min: 40, max: 65,  label: "Refactor / Re-architect",   color: "#7C3AED" },
  Repurchase:  { min: 10, max: 25,  label: "Repurchase (SaaS)",         color: "#D97706" },
  Retire:      { min: 80, max: 100, label: "Retire",                    color: "#16A34A" },
  Retain:      { min: 0,  max: 5,   label: "Retain",                    color: "#71717A" },
  Relocate:    { min: 20, max: 40,  label: "Relocate",                  color: "#06B6D4" },
};

const CLOUD_COLORS = {
  aws:   { color: "#FF9900", bg: "#FFF7ED", border: "#FED7AA", label: "AWS"   },
  azure: { color: "#0078D4", bg: "#F0F9FF", border: "#BAE6FD", label: "Azure" },
  gcp:   { color: "#4285F4", bg: "#EFF6FF", border: "#BFDBFE", label: "GCP"   },
};

function getSavingsEstimate(m) {
  const s = STRATEGY_SAVINGS[m.seven_r_strategy];
  if (!s) return { min: 10, max: 20 };
  return s;
}

function StrategyBar({ label, count, total, color }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
      <div style={{ width: 130, fontSize: 11.5, color: "var(--text-secondary)", flexShrink: 0 }}>{label}</div>
      <div style={{ flex: 1, height: 8, background: "var(--surface-3)", borderRadius: 4, overflow: "hidden" }}>
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          style={{ height: "100%", background: color, borderRadius: 4 }}
        />
      </div>
      <div style={{
        width: 40, textAlign: "right", fontSize: 11.5, fontWeight: 700,
        color: "var(--text-primary)", flexShrink: 0,
      }}>
        {count}
      </div>
      <div style={{ width: 34, fontSize: 10.5, color: "var(--text-tertiary)", flexShrink: 0 }}>
        {pct}%
      </div>
    </div>
  );
}

export default function CostOptimizer() {
  const [migrations, setMigrations] = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);
  const [sortBy, setSortBy]         = useState("savings");

  const load = () => {
    setLoading(true);
    fetchMigrations()
      .then(setMigrations)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const completed  = migrations.filter(m => ["Completed", "Exported"].includes(m.status));
  const allAnalyzed = migrations.filter(m => m.seven_r_strategy);

  /* 7R distribution */
  const stratDist = {};
  for (const m of allAnalyzed) {
    stratDist[m.seven_r_strategy] = (stratDist[m.seven_r_strategy] || 0) + 1;
  }

  /* Cloud target distribution */
  const cloudDist = {};
  for (const m of migrations) {
    const c = m.target_cloud?.toLowerCase();
    if (c) cloudDist[c] = (cloudDist[c] || 0) + 1;
  }

  /* Estimated total savings range */
  const totalMin = allAnalyzed.reduce((acc, m) => acc + getSavingsEstimate(m).min, 0);
  const totalMax = allAnalyzed.reduce((acc, m) => acc + getSavingsEstimate(m).max, 0);

  /* Best strategy by savings */
  const topStrategy = Object.entries(stratDist).sort((a, b) => {
    const sb = (STRATEGY_SAVINGS[b[0]]?.min || 0);
    const sa = (STRATEGY_SAVINGS[a[0]]?.min || 0);
    return (sb * b[1]) - (sa * a[1]);
  })[0];

  /* Table data */
  const tableData = allAnalyzed.map(m => {
    const est = getSavingsEstimate(m);
    return { ...m, savingsMin: est.min, savingsMax: est.max };
  }).sort((a, b) => {
    if (sortBy === "savings") return b.savingsMax - a.savingsMax;
    if (sortBy === "status")  return a.status.localeCompare(b.status);
    return new Date(b.created_at) - new Date(a.created_at);
  });

  return (
    <motion.div
      className="page"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
    >
      <div className="page-header">
        <div>
          <h2 className="page-title">Cost Optimizer</h2>
          <p className="page-subtitle">
            {loading ? "Chargement…" : `${allAnalyzed.length} migration${allAnalyzed.length !== 1 ? "s" : ""} analysées · Estimations basées sur la stratégie 7R`}
          </p>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
            Rafraîchir
          </button>
        </div>
      </div>

      {/* Disclaimer banner — always visible, prominent */}
      <div style={{
        display: "flex", alignItems: "flex-start", gap: 10,
        padding: "11px 16px", marginBottom: 20,
        background: "#fffbeb", border: "1px solid #f59e0b",
        borderRadius: 9, fontSize: 12.5, color: "#92400e",
      }}>
        <span style={{ fontSize: 16, flexShrink: 0 }}>⚠️</span>
        <div>
          <strong>Estimations indicatives uniquement.</strong>{" "}
          Les économies affichées sont calculées à partir des moyennes sectorielles par stratégie 7R
          (source : Gartner, Flexera Cloud Report 2024). Elles ne reflètent pas les tarifs réels
          de votre fournisseur cloud. Utilisez{" "}
          <a href="https://azure.microsoft.com/fr-fr/pricing/calculator/" target="_blank" rel="noreferrer"
            style={{ color: "#0078D4", textDecoration: "underline" }}>
            Azure Pricing Calculator
          </a>{" "}ou{" "}
          <a href="https://calculator.aws/pricing/2/metaindex.json" target="_blank" rel="noreferrer"
            style={{ color: "#FF9900", textDecoration: "underline" }}>
            AWS Pricing Calculator
          </a>{" "}pour des estimations précises.
        </div>
      </div>

      {error && (
        <div style={{
          padding: "10px 14px", borderRadius: 8, marginBottom: 16,
          background: "var(--error-subtle)", border: "1px solid var(--error-border)",
          color: "var(--error-text)", fontSize: 13,
        }}>{error}</div>
      )}

      {/* KPI stats */}
      {!loading && (
        <div className="stats-bar" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: 24 }}>
          {[
            {
              icon: Layers,
              label: "Migrations analysées",
              value: allAnalyzed.length,
              sub: `${migrations.length} total`,
              accent: "blue",
            },
            {
              icon: TrendingDown,
              label: "Économies estimées",
              value: allAnalyzed.length > 0 ? `${totalMin}–${totalMax}%` : "—",
              sub: "réduction de coûts",
              accent: "green",
            },
            {
              icon: CheckCircle,
              label: "Déployées",
              value: completed.length,
              sub: `${migrations.length > 0 ? Math.round((completed.length / migrations.length) * 100) : 0}% taux réussite`,
              accent: "green",
            },
            {
              icon: BarChart2,
              label: "Stratégie leader",
              value: topStrategy ? topStrategy[0] : "—",
              sub: topStrategy ? `${topStrategy[1]} migration${topStrategy[1] !== 1 ? "s" : ""}` : "",
              accent: "purple",
            },
          ].map((s, i) => (
            <motion.div key={s.label} className="stat-card"
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.06 }}
            >
              <div className={`stat-icon ${s.accent}`}><s.icon size={18} strokeWidth={1.8} /></div>
              <div className="stat-info">
                <div className="stat-value" style={{ fontSize: s.value?.length > 6 ? 14 : undefined }}>{s.value}</div>
                <div className="stat-label">{s.label}</div>
                {s.sub && <div style={{ fontSize: 10, color: "var(--text-disabled)", marginTop: 1 }}>{s.sub}</div>}
              </div>
            </motion.div>
          ))}
        </div>
      )}

      {!loading && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
          {/* 7R Distribution */}
          <div style={{
            padding: "16px 20px", borderRadius: 12,
            background: "var(--surface-0)", border: "1px solid var(--surface-4)",
          }}>
            <div style={{
              fontSize: 10, fontWeight: 700, textTransform: "uppercase",
              letterSpacing: "0.1em", color: "var(--text-disabled)", marginBottom: 14,
            }}>
              Distribution des stratégies 7R
            </div>
            {Object.entries(STRATEGY_SAVINGS).map(([key, meta]) => (
              stratDist[key] ? (
                <StrategyBar
                  key={key}
                  label={key}
                  count={stratDist[key]}
                  total={allAnalyzed.length}
                  color={meta.color}
                />
              ) : null
            ))}
            {Object.keys(stratDist).length === 0 && (
              <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                Aucune stratégie assignée. Lancez une analyse pour obtenir des recommandations 7R.
              </p>
            )}
          </div>

          {/* Cloud target breakdown */}
          <div style={{
            padding: "16px 20px", borderRadius: 12,
            background: "var(--surface-0)", border: "1px solid var(--surface-4)",
          }}>
            <div style={{
              fontSize: 10, fontWeight: 700, textTransform: "uppercase",
              letterSpacing: "0.1em", color: "var(--text-disabled)", marginBottom: 14,
            }}>
              Plateformes cibles
            </div>
            {Object.entries(cloudDist).map(([cloud, count]) => {
              const c = CLOUD_COLORS[cloud] || { color: "#6366F1", bg: "#F5F3FF", border: "#DDD6FE", label: cloud };
              const pct = migrations.length > 0 ? Math.round((count / migrations.length) * 100) : 0;
              return (
                <div key={cloud} style={{ marginBottom: 10 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span style={{
                      padding: "2px 8px", borderRadius: 6, fontSize: 11, fontWeight: 700,
                      background: c.bg, color: c.color, border: `1px solid ${c.border}`,
                    }}>{c.label}</span>
                    <span style={{ fontSize: 11, color: "var(--text-tertiary)", marginLeft: "auto" }}>
                      {count} migration{count !== 1 ? "s" : ""} · {pct}%
                    </span>
                  </div>
                  <div style={{ height: 6, background: "var(--surface-3)", borderRadius: 3, overflow: "hidden" }}>
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${pct}%` }}
                      transition={{ duration: 0.6 }}
                      style={{ height: "100%", background: c.color, borderRadius: 3 }}
                    />
                  </div>
                </div>
              );
            })}
            {Object.keys(cloudDist).length === 0 && (
              <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>Aucune migration trouvée.</p>
            )}
          </div>
        </div>
      )}

      {/* Per-migration table */}
      {!loading && tableData.length > 0 && (
        <div style={{
          background: "var(--surface-0)", borderRadius: 12,
          border: "1px solid var(--surface-4)", overflow: "hidden",
        }}>
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "12px 16px", borderBottom: "1px solid var(--surface-3)",
          }}>
            <div style={{
              fontSize: 10, fontWeight: 700, textTransform: "uppercase",
              letterSpacing: "0.1em", color: "var(--text-disabled)",
            }}>
              Analyse par migration ({tableData.length})
            </div>
            <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>Trier par</span>
              {[
                { value: "savings", label: "Économies" },
                { value: "status",  label: "Statut"    },
                { value: "date",    label: "Date"      },
              ].map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setSortBy(opt.value)}
                  style={{
                    padding: "4px 10px", borderRadius: 16, fontSize: 11, cursor: "pointer",
                    border: "1px solid var(--surface-4)",
                    background: sortBy === opt.value ? "var(--brand-500)" : "var(--surface-1)",
                    color: sortBy === opt.value ? "#fff" : "var(--text-secondary)",
                    fontWeight: sortBy === opt.value ? 600 : 400,
                  }}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ background: "var(--surface-2)" }}>
                  {["Migration", "Route", "Stratégie 7R", "Économies estimées", "Statut", ""].map(h => (
                    <th key={h} style={{
                      padding: "8px 14px", textAlign: "left", fontSize: 10, fontWeight: 700,
                      textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--text-tertiary)",
                      whiteSpace: "nowrap",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tableData.map((m, i) => {
                  const sc = CLOUD_COLORS[m.source_cloud?.toLowerCase()] || {};
                  const tc = CLOUD_COLORS[m.target_cloud?.toLowerCase()] || {};
                  const strat = STRATEGY_SAVINGS[m.seven_r_strategy] || {};
                  return (
                    <motion.tr
                      key={m.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: i * 0.03 }}
                      style={{ borderTop: "1px solid var(--surface-3)" }}
                    >
                      <td style={{ padding: "10px 14px" }}>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--text-secondary)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {m.repo_url.replace("https://github.com/", "")}
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-disabled)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                          #{m.id.slice(0, 8)}
                        </div>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
                          <span style={{ fontSize: 11, fontWeight: 700, color: sc.color }}>{(sc.label || m.source_cloud)?.toUpperCase()}</span>
                          <ArrowRight size={10} style={{ color: "var(--text-tertiary)" }} />
                          <span style={{ fontSize: 11, fontWeight: 700, color: tc.color }}>{(tc.label || m.target_cloud)?.toUpperCase()}</span>
                        </div>
                        {m.target_region && (
                          <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 2 }}>📍 {m.target_region}</div>
                        )}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        {m.seven_r_strategy ? (
                          <span style={{
                            padding: "2px 8px", borderRadius: 8, fontSize: 11, fontWeight: 600,
                            background: `${strat.color || "#6366F1"}14`, color: strat.color || "#6366F1",
                            border: `1px solid ${strat.color || "#6366F1"}30`,
                          }}>
                            {m.seven_r_strategy}
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-disabled)", fontSize: 11 }}>—</span>
                        )}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <TrendingDown size={13} style={{ color: "#16A34A" }} />
                          <span style={{ fontWeight: 700, color: "#15803D", fontSize: 12 }}>
                            {m.savingsMin}–{m.savingsMax}%
                          </span>
                        </div>
                        <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 1 }}>
                          réduction mensuelle
                        </div>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <StatusBadge status={m.status} />
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <Link
                          to={`/migrations/${m.id}`}
                          style={{
                            display: "flex", alignItems: "center", gap: 4,
                            fontSize: 11, color: "var(--brand-600)", textDecoration: "none",
                            padding: "3px 8px", borderRadius: 6,
                            background: "var(--brand-50)", border: "1px solid var(--brand-200)",
                          }}
                          onMouseEnter={e => e.currentTarget.style.background = "var(--brand-100)"}
                          onMouseLeave={e => e.currentTarget.style.background = "var(--brand-50)"}
                        >
                          <ExternalLink size={11} /> Voir
                        </Link>
                      </td>
                    </motion.tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!loading && allAnalyzed.length === 0 && (
        <div style={{ textAlign: "center", padding: "60px 24px" }}>
          <DollarSign size={40} style={{ color: "var(--text-disabled)", display: "block", margin: "0 auto 14px" }} />
          <p style={{ fontSize: 14, color: "var(--text-tertiary)", marginBottom: 16 }}>
            Aucune migration analysée. Lancez votre première migration pour obtenir des estimations de coûts.
          </p>
          <Link to="/migrations/new" className="btn btn-primary">Nouvelle Migration</Link>
        </div>
      )}

      {/* Disclaimer */}
      {!loading && allAnalyzed.length > 0 && (
        <div style={{
          marginTop: 16, padding: "10px 14px", borderRadius: 8,
          background: "var(--warning-subtle)", border: "1px solid var(--warning-border)",
          fontSize: 11.5, color: "var(--warning-text)",
        }}>
          Les estimations d'économies sont basées sur des moyennes sectorielles par stratégie 7R. Les coûts réels dépendent de la taille de l'infrastructure et du fournisseur cloud.
        </div>
      )}
    </motion.div>
  );
}
