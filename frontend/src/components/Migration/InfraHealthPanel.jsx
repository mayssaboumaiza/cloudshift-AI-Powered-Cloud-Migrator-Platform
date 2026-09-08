import { useState, useEffect, useRef, useCallback } from "react";
import {
  CheckCircle2, XCircle, Loader2, AlertTriangle, RefreshCw,
  Activity, BarChart2, GitCompare, ChevronDown, ChevronUp,
  Clock, Cpu, Database, HardDrive, Wifi, Server, Zap, Printer,
} from "lucide-react";
import { printElement } from "../../utils/printReport";

// ── Status config ─────────────────────────────────────────────────────────────
const STATUS_CFG = {
  healthy:   { color: "#16a34a", bg: "#f0fdf4", border: "#86efac", label: "Opérationnel",  icon: <CheckCircle2 size={14} /> },
  updating:  { color: "#d97706", bg: "#fffbeb", border: "#fde68a", label: "Mise à jour",   icon: <Loader2 size={14} style={{ animation: "spin 1.5s linear infinite" }} /> },
  creating:  { color: "#2563eb", bg: "#eff6ff", border: "#93c5fd", label: "Création",      icon: <Loader2 size={14} style={{ animation: "spin 1.5s linear infinite" }} /> },
  deleting:  { color: "#dc2626", bg: "#fef2f2", border: "#fca5a5", label: "Suppression",   icon: <Loader2 size={14} style={{ animation: "spin 1.5s linear infinite" }} /> },
  error:     { color: "#dc2626", bg: "#fef2f2", border: "#fca5a5", label: "Erreur",        icon: <XCircle size={14} /> },
  not_found: { color: "#9ca3af", bg: "#f9fafb", border: "#e5e7eb", label: "Introuvable",   icon: <AlertTriangle size={14} /> },
  unknown:   { color: "#6b7280", bg: "#f9fafb", border: "#e5e7eb", label: "Inconnu",       icon: <AlertTriangle size={14} /> },
};

const RESOURCE_ICONS = {
  azurerm_postgresql_flexible_server: <Database size={15} />,
  azurerm_mysql_flexible_server:      <Database size={15} />,
  azurerm_storage_account:            <HardDrive size={15} />,
  azurerm_cognitive_account:          <Zap size={15} />,
  azurerm_search_service:             <Activity size={15} />,
  azurerm_kubernetes_cluster:         <Server size={15} />,
  azurerm_resource_group:             <Server size={15} />,
};

function ResourceIcon({ type }) {
  return RESOURCE_ICONS[type] || <Server size={15} />;
}

// ── Mini sparkline chart ───────────────────────────────────────────────────────
function Sparkline({ values, unit, color = "#3b82f6" }) {
  if (!values || values.length < 2) return <span style={{ fontSize: 10, color: "#9ca3af" }}>—</span>;

  const nums = values.map(v => v.value);
  const min  = Math.min(...nums);
  const max  = Math.max(...nums);
  const range = max - min || 1;

  const W = 80, H = 28, pad = 2;
  const pts = nums.map((v, i) => {
    const x = pad + (i / (nums.length - 1)) * (W - 2 * pad);
    const y = H - pad - ((v - min) / range) * (H - 2 * pad);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  return (
    <svg width={W} height={H} style={{ verticalAlign: "middle" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      <circle
        cx={parseFloat(pts.split(" ").at(-1).split(",")[0])}
        cy={parseFloat(pts.split(" ").at(-1).split(",")[1])}
        r={2.5} fill={color}
      />
    </svg>
  );
}

// ── Metric row ─────────────────────────────────────────────────────────────────
function MetricRow({ metric }) {
  const latest = metric.latest;
  const pct = metric.unit === "%" ? latest : null;
  const barColor = pct !== null
    ? (pct > 90 ? "#ef4444" : pct > 70 ? "#f59e0b" : "#22c55e")
    : "#3b82f6";

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "6px 0", borderBottom: "1px solid var(--surface-3,#eef1f7)",
      fontSize: 12,
    }}>
      <span style={{ width: 130, color: "var(--text-secondary)", fontWeight: 500, flexShrink: 0 }}>
        {metric.label}
      </span>
      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 6 }}>
        {pct !== null ? (
          <div style={{ flex: 1, height: 6, background: "#e5e7eb", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ width: `${Math.min(pct, 100)}%`, height: "100%", background: barColor, borderRadius: 3, transition: "width 0.5s" }} />
          </div>
        ) : (
          <Sparkline values={metric.values} unit={metric.unit} color={barColor} />
        )}
      </div>
      <span style={{ fontWeight: 700, color: barColor, width: 70, textAlign: "right", flexShrink: 0 }}>
        {latest !== null && latest !== undefined
          ? `${latest.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} ${metric.unit}`
          : "—"}
      </span>
    </div>
  );
}

// ── Resource card ──────────────────────────────────────────────────────────────
function ResourceCard({ resource, metrics }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = STATUS_CFG[resource.health_status] || STATUS_CFG.unknown;
  const shortType = resource.type.replace("azurerm_", "").replace(/_/g, " ");
  const resourceKey = `${resource.type}.${resource.name}`;
  const resourceMetrics = metrics?.[resourceKey] || [];

  return (
    <div style={{
      background: cfg.bg,
      border: `1px solid ${cfg.border}`,
      borderRadius: 9,
      overflow: "hidden",
      marginBottom: 10,
      transition: "box-shadow 200ms",
    }}>
      {/* Header row */}
      <div
        style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "11px 14px", cursor: "pointer",
        }}
        onClick={() => setExpanded(v => !v)}
      >
        <span style={{ color: cfg.color, display: "flex", alignItems: "center", gap: 4 }}>
          {cfg.icon}
        </span>
        <ResourceIcon type={resource.type} />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)" }}>
            {resource.display_name || resource.name}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            {shortType}
            {resource.resource_group_name && ` · ${resource.resource_group_name}`}
          </div>
        </div>
        <span style={{
          padding: "2px 9px", borderRadius: 10, fontSize: 11, fontWeight: 700,
          background: cfg.color + "20", color: cfg.color,
        }}>
          {cfg.label}
        </span>
        <span style={{ color: "var(--text-tertiary)" }}>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div style={{ padding: "0 14px 14px", background: "rgba(255,255,255,0.6)" }}>

          {/* Provisioning state */}
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 10 }}>
            <strong>Provisioning state :</strong>{" "}
            <code style={{ fontSize: 11 }}>{resource.provisioning_state}</code>
            {resource.location && (
              <span style={{ marginLeft: 10 }}>
                <strong>Région :</strong> {resource.location}
              </span>
            )}
          </div>

          {/* Error */}
          {resource.error && (
            <div style={{
              padding: "8px 10px", background: "#fef2f2", border: "1px solid #fca5a5",
              borderRadius: 6, fontSize: 11, color: "#991b1b",
              fontFamily: "monospace", whiteSpace: "pre-wrap", marginBottom: 10,
            }}>
              {resource.error.slice(0, 400)}
            </div>
          )}

          {/* Metrics */}
          {resourceMetrics.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontWeight: 700, fontSize: 11, color: "var(--text-tertiary)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                <BarChart2 size={11} style={{ verticalAlign: "middle", marginRight: 4 }} />
                Métriques (dernière heure)
              </div>
              {resourceMetrics.map((m, i) => <MetricRow key={i} metric={m} />)}
            </div>
          )}

          {/* TFState attributes */}
          {Object.keys(resource.tfstate_attrs || {}).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 11, color: "var(--text-tertiary)", marginBottom: 4, textTransform: "uppercase" }}>
                État Terraform
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {Object.entries(resource.tfstate_attrs).map(([k, v]) => (
                  <span key={k} style={{
                    padding: "2px 8px", borderRadius: 8,
                    background: "var(--surface-2,#f4f6fa)",
                    fontSize: 11, color: "var(--text-secondary)",
                  }}>
                    <strong>{k}</strong>: {String(v)}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Drift panel ────────────────────────────────────────────────────────────────
function DriftPanel({ drifts, summary }) {
  if (!drifts || drifts.length === 0) {
    // "0 drift" is only meaningful if resources were actually checked against ARM.
    // Resources in unknown/error state are SKIPPED by detect_drift, so a green
    // "no drift" banner would be misleading when nothing could be verified.
    const checkable = summary
      ? (summary.total || 0) - (summary.unknown || 0) - (summary.error || 0) - (summary.not_found || 0)
      : 1;
    if (checkable <= 0) {
      return (
        <div style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "12px 14px", background: "#f9fafb", border: "1px solid #e5e7eb",
          borderRadius: 8, fontSize: 13, color: "#6b7280", fontWeight: 600,
        }}>
          <AlertTriangle size={16} />
          Drift non vérifié — aucune ressource n&apos;a pu être interrogée sur Azure
          (statut « Inconnu »). Vérifiez les credentials / le SDK Azure.
        </div>
      );
    }
    return (
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "12px 14px", background: "#f0fdf4", border: "1px solid #86efac",
        borderRadius: 8, fontSize: 13, color: "#15803d", fontWeight: 600,
      }}>
        <CheckCircle2 size={16} />
        Aucun drift détecté — la configuration live correspond au tfstate.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {drifts.map((d, i) => (
        <div key={i} style={{
          padding: "12px 14px", background: "#fffbeb", border: "1px solid #fde68a",
          borderRadius: 8, fontSize: 12,
        }}>
          <div style={{ fontWeight: 700, color: "#92400e", marginBottom: 6 }}>
            <AlertTriangle size={13} style={{ verticalAlign: "middle", marginRight: 4 }} />
            {d.resource_type?.replace("azurerm_", "") || "?"} — {d.resource_name}
          </div>
          {d.drifts.map((dd, j) => (
            <div key={j} style={{ display: "flex", gap: 8, alignItems: "center", padding: "3px 0" }}>
              <code style={{ fontSize: 11, fontWeight: 700, color: "#78350f" }}>{dd.attribute}</code>
              <span style={{ color: "#6b7280", fontSize: 11 }}>tfstate:</span>
              <code style={{ fontSize: 11, color: "#16a34a" }}>{dd.expected}</code>
              <span style={{ color: "#6b7280" }}>→</span>
              <span style={{ color: "#6b7280", fontSize: 11 }}>live:</span>
              <code style={{ fontSize: 11, color: "#dc2626" }}>{dd.actual}</code>
              <span style={{
                padding: "1px 6px", borderRadius: 8, fontSize: 10, fontWeight: 700,
                background: dd.severity === "high" ? "#fef2f2" : "#fffbeb",
                color:      dd.severity === "high" ? "#dc2626" : "#d97706",
              }}>{dd.severity?.toUpperCase()}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// ── Summary bar ────────────────────────────────────────────────────────────────
function SummaryBar({ summary }) {
  if (!summary || summary.total === 0) return null;

  const items = [
    { key: "healthy",   label: "Opérationnel", color: "#16a34a", bg: "#dcfce7" },
    { key: "updating",  label: "En cours",      color: "#d97706", bg: "#fef9c3" },
    { key: "error",     label: "Erreur",         color: "#dc2626", bg: "#fef2f2" },
    { key: "not_found", label: "Introuvable",    color: "#9ca3af", bg: "#f3f4f6" },
  ];

  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
      {items.map(({ key, label, color, bg }) =>
        (summary[key] || 0) > 0 ? (
          <div key={key} style={{
            padding: "5px 12px", borderRadius: 20, fontSize: 12, fontWeight: 700,
            background: bg, color,
          }}>
            {summary[key]} {label}
          </div>
        ) : null
      )}
      <div style={{
        marginLeft: "auto", padding: "5px 12px", borderRadius: 20,
        fontSize: 12, color: "var(--text-tertiary)",
        background: "var(--surface-2,#f4f6fa)",
      }}>
        {summary.total} ressource{summary.total > 1 ? "s" : ""}
      </div>
    </div>
  );
}

// ── Budget tracker ─────────────────────────────────────────────────────────────
// EUR/USD rate is configurable via VITE_USD_EUR_RATE (defaults to 0.92) so the
// budget figures aren't pinned to a stale hardcoded exchange rate.
const USD_EUR_RATE = Number(import.meta.env?.VITE_USD_EUR_RATE) || 0.92;

function BudgetTracker({ monthlyCostEur, deployedAt, budgetUsd = 100 }) {
  const budgetEur = budgetUsd * USD_EUR_RATE;
  const now = new Date();
  const start = deployedAt ? new Date(deployedAt) : now;
  const daysDeployed = Math.max(0, (now - start) / (1000 * 60 * 60 * 24));
  const spent = monthlyCostEur ? (monthlyCostEur / 30) * daysDeployed : 0;
  const pct = Math.min(100, (spent / budgetEur) * 100);
  const dailyRate = monthlyCostEur ? monthlyCostEur / 30 : 0;
  const daysLeft = dailyRate > 0 ? Math.floor((budgetEur - spent) / dailyRate) : 999;

  const color = pct > 80 ? "#dc2626" : pct > 50 ? "#d97706" : "#16a34a";
  const bg    = pct > 80 ? "#fef2f2" : pct > 50 ? "#fffbeb" : "#f0fdf4";

  return (
    <div style={{ padding: "14px 16px", background: bg, borderRadius: 10, border: `1px solid ${color}30`, marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 16 }}>💰</span>
        <span style={{ fontWeight: 700, fontSize: 13, color }}>Budget tracker</span>
        <span style={{ marginLeft: "auto", fontSize: 12, fontWeight: 700, color }}>
          {spent.toFixed(2)} € / {budgetEur.toFixed(0)} € ({pct.toFixed(0)}%)
        </span>
      </div>
      <div style={{ height: 8, background: "#e5e7eb", borderRadius: 4, overflow: "hidden", marginBottom: 8 }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 4, transition: "width 0.5s" }} />
      </div>
      <div style={{ display: "flex", gap: 16, fontSize: 11, color: "var(--text-secondary)" }}>
        <span>📅 {daysDeployed.toFixed(1)} jours déployés</span>
        <span>🔥 {dailyRate.toFixed(2)} €/jour</span>
        <span style={{ color: daysLeft < 10 ? "#dc2626" : "inherit", fontWeight: daysLeft < 10 ? 700 : 400 }}>
          ⏳ ~{daysLeft} jours restants dans le budget
        </span>
      </div>
      {pct > 75 && (
        <div style={{ marginTop: 8, fontSize: 12, fontWeight: 600, color, padding: "6px 10px", background: color + "15", borderRadius: 6 }}>
          ⚠️ {pct > 90 ? "CRITIQUE" : "ATTENTION"} — Pense à détruire l'infrastructure pour ne pas dépasser ton budget !
        </div>
      )}
    </div>
  );
}

// ── Destroy section ────────────────────────────────────────────────────────────
function DestroySection({ migrationId }) {
  const [confirm, setConfirm]   = useState(false);
  const [loading, setLoading]   = useState(false);
  const [result,  setResult]    = useState(null);

  const handleDestroy = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/migrations/${migrationId}/infra-destroy`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Erreur");
      setResult({ ok: true, msg: `Destroy lancé — Job ID: ${data.job_id}. Suivi dans l'onglet Déploiement.` });
    } catch (e) {
      setResult({ ok: false, msg: e.message });
    } finally {
      setLoading(false);
      setConfirm(false);
    }
  };

  if (result) return (
    <div style={{
      padding: "12px 16px", borderRadius: 8, fontSize: 13,
      background: result.ok ? "#f0fdf4" : "#fef2f2",
      border: `1px solid ${result.ok ? "#86efac" : "#fca5a5"}`,
      color: result.ok ? "#15803d" : "#dc2626", fontWeight: 600,
    }}>
      {result.ok ? "✅" : "❌"} {result.msg}
    </div>
  );

  return (
    <div style={{ padding: "14px 16px", background: "#fef2f2", borderRadius: 10, border: "1px solid #fca5a5" }}>
      <div style={{ fontWeight: 700, fontSize: 13, color: "#dc2626", marginBottom: 6, display: "flex", alignItems: "center", gap: 6 }}>
        🗑️ Détruire l'infrastructure
      </div>
      <div style={{ fontSize: 12, color: "#7f1d1d", marginBottom: 10, lineHeight: 1.5 }}>
        Supprime toutes les ressources Azure créées par cette migration. <strong>Action irréversible.</strong>
        Les coûts s'arrêtent immédiatement après la destruction.
      </div>

      {!confirm ? (
        <button
          onClick={() => setConfirm(true)}
          style={{
            padding: "7px 16px", borderRadius: 7, cursor: "pointer",
            background: "#dc2626", color: "#fff", border: "none",
            fontWeight: 700, fontSize: 12,
          }}
        >
          Détruire l'infrastructure
        </button>
      ) : (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: "#dc2626" }}>Confirmer la destruction ?</span>
          <button
            onClick={handleDestroy}
            disabled={loading}
            style={{
              padding: "6px 14px", borderRadius: 6, cursor: "pointer",
              background: "#dc2626", color: "#fff", border: "none",
              fontWeight: 700, fontSize: 12,
              opacity: loading ? 0.6 : 1,
            }}
          >
            {loading ? "En cours…" : "Oui, détruire"}
          </button>
          <button
            onClick={() => setConfirm(false)}
            style={{
              padding: "6px 14px", borderRadius: 6, cursor: "pointer",
              background: "var(--surface-2)", color: "var(--text-primary)",
              border: "1px solid var(--surface-4)", fontWeight: 600, fontSize: 12,
            }}
          >
            Annuler
          </button>
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function InfraHealthPanel({ migrationId, monthlyCostEur, deployedAt, budgetUsd }) {
  const [data,        setData]        = useState(null);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [activeView,  setActiveView]  = useState("resources"); // resources | drift
  const [downAlert,   setDownAlert]   = useState(null); // { resource, prev, curr }
  const prevSummaryRef = useRef(null);
  const intervalRef    = useRef(null);
  const panelRef       = useRef(null);

  const fetchHealth = useCallback(async () => {
    if (!migrationId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/v1/migrations/${migrationId}/infra-health?cached=true`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();

      // ── DOWN detection ──
      // Two triggers:
      //   (a) a NEW error appeared since the last poll (transition healthy→error)
      //   (b) errors are ALREADY present on the very first load (page opened on a
      //       down infra) — prevSummaryRef is null then, so (a) alone would miss it.
      if (d.summary) {
        const prev = prevSummaryRef.current;
        const currErrors = (d.summary.error || 0) + (d.summary.not_found || 0);
        const prevErrors = prev ? (prev.error || 0) + (prev.not_found || 0) : 0;
        const newErrors = prev ? currErrors - prevErrors : currErrors;
        if (newErrors > 0) {
          const downResources = (d.resources || []).filter(
            r => r.health_status === "error" || r.health_status === "not_found"
          );
          // Group by resource category (azurerm_* type) so the user can see at
          // a glance WHICH KIND of service is impacted (DB, storage, AI, …)
          // rather than a single named resource — matches the categorized
          // alert display requested for the monitoring section.
          const byCategory = {};
          for (const r of downResources) {
            const cat = r.type || "unknown";
            (byCategory[cat] = byCategory[cat] || []).push(r.display_name || r.name || "ressource");
          }
          setDownAlert({
            categories: Object.entries(byCategory).map(([type, names]) => ({ type, names })),
            count: newErrors,
            ts: new Date().toLocaleTimeString("fr-FR"),
          });
        }
      }
      if (d.summary) prevSummaryRef.current = d.summary;

      setData(d);
      setLastRefresh(new Date());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [migrationId]);

  // Initial fetch
  useEffect(() => {
    fetchHealth();
  }, [fetchHealth]);

  // Auto-refresh every 30s
  useEffect(() => {
    intervalRef.current = setInterval(fetchHealth, 30_000);
    return () => clearInterval(intervalRef.current);
  }, [fetchHealth]);

  // ── Render ─────────────────────────────────────────────────────────────────

  if (!data && loading) return (
    <div style={{ padding: 32, textAlign: "center", color: "var(--text-tertiary)" }}>
      <Loader2 size={24} style={{ animation: "spin 1s linear infinite", marginBottom: 8 }} />
      <div>Connexion à Azure Resource Manager…</div>
    </div>
  );

  if (error) return (
    <div style={{ padding: "14px 16px", background: "#fef2f2", borderRadius: 8, color: "#dc2626", fontSize: 13 }}>
      <strong>Erreur :</strong> {error}
      <button onClick={fetchHealth} style={{ marginLeft: 10, cursor: "pointer", textDecoration: "underline", background: "none", border: "none", color: "#dc2626" }}>
        Réessayer
      </button>
    </div>
  );

  if (!data) return null;

  // No tfstate yet
  if (!data.tfstate_available) return (
    <div style={{ padding: "20px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>
      <Activity size={24} style={{ marginBottom: 8, opacity: 0.4 }} />
      <div style={{ fontWeight: 600 }}>Aucune infrastructure déployée</div>
      <div style={{ fontSize: 12, marginTop: 4 }}>{data.message}</div>
    </div>
  );

  // Credentials missing
  if (!data.health_checks) return (
    <div style={{ padding: "16px", background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 8, fontSize: 13 }}>
      <AlertTriangle size={16} color="#d97706" style={{ verticalAlign: "middle", marginRight: 6 }} />
      <strong style={{ color: "#92400e" }}>Credentials Azure non disponibles</strong>
      <div style={{ color: "#92400e", marginTop: 4, fontSize: 12 }}>
        {data.resources.length} ressource(s) dans le tfstate — impossible de contacter l&apos;API Azure.
      </div>
    </div>
  );

  const resources = data.resources || [];
  const metrics   = data.metrics   || {};
  const drifts    = data.drift_detected || [];

  return (
    <div ref={panelRef} style={{ display: "flex", flexDirection: "column", gap: 14 }}>

      {/* DOWN alert banner — grouped by resource category */}
      {downAlert && (
        <div style={{
          display: "flex", flexDirection: "column", gap: 8,
          padding: "12px 16px", background: "#fef2f2",
          border: "1px solid #f87171", borderRadius: 8,
          fontSize: 13, color: "#dc2626",
          animation: "alertPulse 2s ease infinite",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, fontWeight: 700 }}>
            <XCircle size={16} />
            🚨 INFRA DOWN — {downAlert.count} ressource(s) en erreur à {downAlert.ts}
            <button onClick={() => setDownAlert(null)} style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "#dc2626" }}>✕</button>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingLeft: 26 }}>
            {downAlert.categories.map(({ type, names }) => (
              <div key={type} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                <span style={{
                  display: "inline-flex", alignItems: "center", gap: 5,
                  padding: "2px 8px", borderRadius: 6,
                  background: "#fee2e2", border: "1px solid #fca5a5",
                  fontWeight: 600, color: "#991b1b",
                }}>
                  <ResourceIcon type={type} />
                  {type.replace("azurerm_", "").replace(/_/g, " ")}
                </span>
                <span style={{ color: "#b91c1c" }}>{names.join(", ")}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Budget tracker */}
      {monthlyCostEur > 0 && (
        <BudgetTracker
          monthlyCostEur={monthlyCostEur}
          deployedAt={deployedAt}
          budgetUsd={budgetUsd || 100}
        />
      )}

      {/* Header bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div style={{ fontWeight: 700, fontSize: 14, display: "flex", alignItems: "center", gap: 6 }}>
          <Activity size={16} color="#3b82f6" />
          Monitoring Infrastructure Azure
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {lastRefresh && (
            <span style={{ fontSize: 11, color: "var(--text-tertiary)", display: "flex", alignItems: "center", gap: 4 }}>
              <Clock size={11} />
              {lastRefresh.toLocaleTimeString("fr-FR")} · auto-refresh 30s
            </span>
          )}
          <button
            onClick={() => printElement(panelRef.current, "Health Report Infrastructure")}
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "4px 10px", borderRadius: 6, cursor: "pointer",
              background: "var(--surface-2,#f4f6fa)",
              border: "1px solid var(--surface-4,#e4e8f0)",
              fontSize: 12, fontWeight: 600, color: "var(--text-secondary)",
            }}
          >
            <Printer size={11} /> PDF
          </button>
          <button
            onClick={fetchHealth}
            disabled={loading}
            style={{
              display: "flex", alignItems: "center", gap: 4,
              padding: "4px 10px", borderRadius: 6, cursor: "pointer",
              background: "var(--surface-2,#f4f6fa)",
              border: "1px solid var(--surface-4,#e4e8f0)",
              fontSize: 12, fontWeight: 600, color: "var(--text-secondary)",
            }}
          >
            <RefreshCw size={11} style={{ animation: loading ? "spin 0.8s linear infinite" : "none" }} />
            Actualiser
          </button>
        </div>
      </div>

      {/* Summary bar */}
      <SummaryBar summary={data.summary} />

      {/* Drift alert banner */}
      {drifts.length > 0 && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 14px", background: "#fffbeb", border: "1px solid #fde68a",
          borderRadius: 8, fontSize: 13,
        }}>
          <AlertTriangle size={16} color="#d97706" />
          <span style={{ fontWeight: 700, color: "#92400e" }}>
            {drifts.length} drift(s) détecté(s)
          </span>
          <span style={{ color: "#78350f", fontSize: 12 }}>
            La configuration live ne correspond pas au tfstate. Exécutez <code>terraform apply</code> pour resynchroniser.
          </span>
        </div>
      )}

      {/* Tab switcher */}
      <div style={{ display: "flex", gap: 6 }}>
        {[
          { id: "resources", label: `Ressources (${resources.length})`, icon: <Server size={13} /> },
          { id: "drift",     label: `Drift (${drifts.length})`,         icon: <GitCompare size={13} /> },
        ].map(({ id, label, icon }) => (
          <button
            key={id}
            onClick={() => setActiveView(id)}
            style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "5px 12px", borderRadius: 20, cursor: "pointer",
              fontSize: 12, fontWeight: 600,
              background: activeView === id ? "#3b82f6" : "var(--surface-2,#f4f6fa)",
              color:      activeView === id ? "#fff"    : "var(--text-secondary)",
              border:     activeView === id ? "1px solid #3b82f6" : "1px solid var(--surface-4,#e4e8f0)",
            }}
          >
            {icon} {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {activeView === "resources" && (
        <div>
          {resources.length === 0 ? (
            <div style={{ textAlign: "center", color: "var(--text-tertiary)", padding: 20 }}>
              Aucune ressource managée dans le tfstate.
            </div>
          ) : (
            resources.map((r, i) => (
              <ResourceCard key={r.id || i} resource={r} metrics={metrics} />
            ))
          )}
        </div>
      )}

      {activeView === "drift" && <DriftPanel drifts={drifts} summary={data.summary} />}

      {/* Destroy section — always visible once infra is deployed */}
      <DestroySection migrationId={migrationId} />
    </div>
  );
}
