import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, AlertTriangle, ShieldAlert, Zap, X, ChevronRight, RefreshCw, CheckCircle2 } from "lucide-react";

const SEV = {
  HIGH:   { color: "#dc2626", bg: "#fef2f2", border: "#fca5a5", badge: "#ef4444", dot: "#ef4444", label: "HIGH",   icon: <ShieldAlert size={13} /> },
  MEDIUM: { color: "#d97706", bg: "#fffbeb", border: "#fde68a", badge: "#f59e0b", dot: "#f59e0b", label: "MEDIUM", icon: <AlertTriangle size={13} /> },
  LOW:    { color: "#2563eb", bg: "#eff6ff", border: "#93c5fd", badge: "#3b82f6", dot: "#3b82f6", label: "LOW",    icon: <Zap size={13} /> },
};

const CAT_LABELS = {
  security:   "Sécurité IaC",
  iac:        "Terraform",
  intent:     "Intent",
  deployment: "Déploiement",
  pipeline:   "Pipeline",
  quality:    "Qualité",
  health:     "🔴 Infra Health",
};

export default function AlertsCenter() {
  const [open,    setOpen]    = useState(false);
  const [data,    setData]    = useState({ alerts: [], counts: { HIGH: 0, MEDIUM: 0, LOW: 0, total: 0 } });
  const [filter,  setFilter]  = useState("ALL");
  const [loading, setLoading] = useState(false);
  const ref = useRef(null);
  const navigate = useNavigate();

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/alerts");
      if (res.ok) setData(await res.json());
    } catch (_) {}
    finally { setLoading(false); }
  }, []);

  // Lightweight badge poll every 30s (when closed)
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await fetch("/api/v1/alerts/summary");
        if (res.ok) {
          const counts = await res.json();
          setData(prev => ({ ...prev, counts }));
        }
      } catch (_) {}
    };
    poll();
    const id = setInterval(poll, 30_000);
    return () => clearInterval(id);
  }, []);

  // Full fetch on open
  useEffect(() => {
    if (open) fetchAlerts();
  }, [open, fetchAlerts]);

  // Close on outside click
  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const total     = data.counts?.total || 0;
  const highCount = data.counts?.HIGH  || 0;

  const filtered = filter === "ALL"
    ? (data.alerts || [])
    : (data.alerts || []).filter(a => a.severity === filter);

  return (
    <div ref={ref} style={{ position: "relative" }}>

      {/* Bell button */}
      <button
        onClick={() => setOpen(v => !v)}
        style={{
          position: "relative",
          width: 34, height: 34,
          display: "flex", alignItems: "center", justifyContent: "center",
          borderRadius: 8, cursor: "pointer",
          background: open ? "var(--surface-3,#eef1f7)" : "transparent",
          border: "1px solid " + (open ? "var(--surface-4,#e4e8f0)" : "transparent"),
          color: highCount > 0 ? "#dc2626" : "var(--text-tertiary)",
          transition: "all 150ms ease",
        }}
        title={`${total} alerte(s) active(s)`}
      >
        <Bell size={16} style={{ fill: highCount > 0 ? "#fef2f2" : "none" }} />
        {total > 0 && (
          <span style={{
            position: "absolute", top: 3, right: 3,
            width: 16, height: 16,
            background: highCount > 0 ? "#ef4444" : "#f59e0b",
            color: "#fff", borderRadius: "50%",
            fontSize: 9, fontWeight: 800,
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 0 0 2px var(--surface-0,#fff)",
            animation: highCount > 0 ? "alertPulse 2s ease infinite" : "none",
          }}>
            {total > 99 ? "99+" : total}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 8px)", right: 0,
          width: 400, maxHeight: 520,
          background: "var(--surface-0,#fff)",
          border: "1px solid var(--surface-4,#e4e8f0)",
          borderRadius: 12,
          boxShadow: "0 8px 40px rgba(0,0,0,0.14), 0 2px 12px rgba(0,0,0,0.08)",
          display: "flex", flexDirection: "column",
          zIndex: 9999,
          animation: "slideDownFade 150ms ease",
          overflow: "hidden",
        }}>

          {/* Header */}
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "12px 14px",
            borderBottom: "1px solid var(--surface-3,#eef1f7)",
            background: "var(--surface-1,#fafafa)",
          }}>
            <Bell size={15} color="var(--text-secondary)" />
            <span style={{ fontWeight: 700, fontSize: 13 }}>Alertes système</span>
            <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
              {[
                { k: "HIGH",   color: "#ef4444" },
                { k: "MEDIUM", color: "#f59e0b" },
                { k: "LOW",    color: "#3b82f6" },
              ].map(({ k, color }) => data.counts?.[k] > 0 && (
                <span key={k} style={{
                  padding: "1px 7px", borderRadius: 10, fontSize: 10, fontWeight: 800,
                  background: color + "15", color,
                }}>{data.counts[k]} {k}</span>
              ))}
            </div>
            <button onClick={fetchAlerts} title="Actualiser" style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-tertiary)", display: "flex", padding: 2 }}>
              <RefreshCw size={12} style={{ animation: loading ? "spin 0.8s linear infinite" : "none" }} />
            </button>
            <button onClick={() => setOpen(false)} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-tertiary)", display: "flex", padding: 2 }}>
              <X size={13} />
            </button>
          </div>

          {/* Severity filter tabs */}
          <div style={{
            display: "flex", gap: 4, padding: "8px 12px",
            borderBottom: "1px solid var(--surface-3,#eef1f7)",
            background: "var(--surface-1,#fafafa)",
          }}>
            {["ALL", "HIGH", "MEDIUM", "LOW"].map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  padding: "3px 10px", borderRadius: 20, cursor: "pointer",
                  fontSize: 11, fontWeight: 600,
                  background: filter === f
                    ? (f === "HIGH" ? "#fef2f2" : f === "MEDIUM" ? "#fffbeb" : f === "LOW" ? "#eff6ff" : "var(--surface-3)")
                    : "transparent",
                  color: filter === f
                    ? (f === "HIGH" ? "#dc2626" : f === "MEDIUM" ? "#d97706" : f === "LOW" ? "#2563eb" : "var(--text-primary)")
                    : "var(--text-tertiary)",
                  border: filter === f ? `1px solid ${f === "HIGH" ? "#fca5a5" : f === "MEDIUM" ? "#fde68a" : f === "LOW" ? "#93c5fd" : "var(--surface-4)"}` : "1px solid transparent",
                }}
              >
                {f === "ALL" ? `Tout (${total})` : `${f} (${data.counts?.[f] || 0})`}
              </button>
            ))}
          </div>

          {/* Alerts list */}
          <div style={{ flex: 1, overflowY: "auto", padding: "6px 0" }}>
            {filtered.length === 0 ? (
              <div style={{
                padding: "32px 20px", textAlign: "center",
                color: "var(--text-tertiary)", fontSize: 13,
              }}>
                <CheckCircle2 size={28} style={{ marginBottom: 8, opacity: 0.4, color: "#16a34a" }} />
                <div style={{ fontWeight: 600 }}>Aucune alerte {filter !== "ALL" ? filter : ""}</div>
                <div style={{ fontSize: 11, marginTop: 4 }}>Toute l'infrastructure est opérationnelle.</div>
              </div>
            ) : (
              filtered.map(alert => {
                const cfg = SEV[alert.severity] || SEV.LOW;
                return (
                  <div
                    key={alert.id}
                    onClick={() => {
                      navigate(`/migrations/${alert.migration_id}`);
                      setOpen(false);
                    }}
                    style={{
                      padding: "10px 14px",
                      cursor: "pointer",
                      borderLeft: `3px solid ${cfg.dot}`,
                      marginLeft: 2, marginBottom: 1,
                      background: "transparent",
                      transition: "background 120ms",
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = cfg.bg}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                  >
                    <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                      <span style={{ color: cfg.color, marginTop: 1, flexShrink: 0 }}>{cfg.icon}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                          <span style={{ fontWeight: 700, fontSize: 12, color: "var(--text-primary)" }}>
                            {alert.title}
                          </span>
                          <span style={{
                            padding: "1px 6px", borderRadius: 8, fontSize: 10, fontWeight: 700,
                            background: cfg.color + "15", color: cfg.color,
                          }}>{cfg.label}</span>
                          <span style={{
                            padding: "1px 6px", borderRadius: 8, fontSize: 10,
                            background: "var(--surface-2,#f4f6fa)", color: "var(--text-tertiary)",
                          }}>{CAT_LABELS[alert.category] || alert.category}</span>
                        </div>
                        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 3, lineHeight: 1.4 }}>
                          {alert.message?.slice(0, 120)}
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5, flexWrap: "wrap" }}>
                          <span style={{
                            padding: "1px 7px", borderRadius: 6, fontSize: 10, fontWeight: 700,
                            background: "var(--surface-2,#f4f6fa)",
                            color: "var(--text-secondary)",
                            border: "1px solid var(--surface-4,#e4e8f0)",
                            fontFamily: "var(--font-mono)",
                          }}>
                            {alert.repo || alert.migration_id?.slice(0, 8)}
                          </span>
                          {alert.migration_id && (
                            <span style={{ fontSize: 10, color: "var(--text-disabled)", fontFamily: "var(--font-mono)" }}>
                              #{alert.migration_id.slice(0, 8)}
                            </span>
                          )}
                          {alert.resource && (
                            <span style={{ fontSize: 10, color: "var(--text-disabled)" }}>
                              · {alert.resource}
                            </span>
                          )}
                          <ChevronRight size={10} style={{ marginLeft: "auto", color: "var(--text-disabled)" }} />
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Footer */}
          {filtered.length > 0 && (
            <div style={{
              padding: "8px 14px",
              borderTop: "1px solid var(--surface-3,#eef1f7)",
              fontSize: 11, color: "var(--text-tertiary)",
              background: "var(--surface-1,#fafafa)",
              textAlign: "center",
            }}>
              {filtered.length} alerte(s) — cliquez pour ouvrir la migration
            </div>
          )}
        </div>
      )}
    </div>
  );
}
