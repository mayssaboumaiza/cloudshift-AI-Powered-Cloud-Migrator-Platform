import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Link } from "react-router-dom";
import {
  FileCode2, RefreshCw, GitBranch, CheckCircle, Download,
  ChevronRight, Package, ExternalLink, ArrowRight, Server,
  Database, Shield, Cloud,
} from "lucide-react";
import { fetchMigrations, fetchMigration } from "../api/migrationApi";
import StatusBadge from "../components/common/StatusBadge";

const IaC_STATUSES = ["IaC_Ready", "Completed", "Exported", "Deploying", "Health_Checking"];

const CLOUD_COLORS = {
  aws:   { color: "#FF9900", label: "AWS"   },
  azure: { color: "#0078D4", label: "Azure" },
  gcp:   { color: "#4285F4", label: "GCP"   },
};

/* Map service names to resource types for visual grouping */
function guessResourceType(name = "") {
  const n = name.toLowerCase();
  if (n.includes("db") || n.includes("sql") || n.includes("postgres") || n.includes("database") || n.includes("cosmos")) return "database";
  if (n.includes("storage") || n.includes("blob") || n.includes("s3") || n.includes("bucket")) return "storage";
  if (n.includes("function") || n.includes("lambda") || n.includes("serverless")) return "serverless";
  if (n.includes("container") || n.includes("aks") || n.includes("eks") || n.includes("ecs") || n.includes("k8s")) return "container";
  return "compute";
}

const TYPE_META = {
  database:   { icon: Database, color: "#7C3AED", label: "Base de données" },
  storage:    { icon: Package,  color: "#0284C7", label: "Stockage"        },
  serverless: { icon: Cloud,    color: "#06B6D4", label: "Serverless"      },
  container:  { icon: Server,   color: "#D97706", label: "Container"       },
  compute:    { icon: Server,   color: "#16A34A", label: "Compute"         },
};

function MigrationRow({ m, selected, onSelect }) {
  const isIaC = IaC_STATUSES.includes(m.status);
  const tc = CLOUD_COLORS[m.target_cloud?.toLowerCase()] || { color: "#6366F1", label: m.target_cloud };

  return (
    <div
      onClick={onSelect}
      style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 12px", borderRadius: 9, cursor: "pointer",
        background: selected ? "var(--sidebar-active-bg)" : "transparent",
        borderLeft: selected ? "2px solid var(--brand-500)" : "2px solid transparent",
        marginLeft: selected ? -2 : 0,
        transition: "all 130ms",
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = "var(--surface-3)"; }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = "transparent"; }}
    >
      <FileCode2 size={14} style={{ color: isIaC ? "var(--brand-500)" : "var(--text-disabled)", flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 12, fontFamily: "var(--font-mono)",
          color: "var(--text-secondary)", overflow: "hidden",
          textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {m.repo_url.replace("https://github.com/", "")}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
          <span style={{ fontSize: 10, color: "var(--text-disabled)", fontFamily: "var(--font-mono)" }}>
            #{m.id.slice(0, 8)}
          </span>
          <span style={{ fontSize: 9.5, fontWeight: 700, color: tc.color }}>→ {tc.label}</span>
        </div>
      </div>
      <StatusBadge status={m.status} />
    </div>
  );
}

function ResourceCard({ resource, index }) {
  const type = guessResourceType(resource.target_service || resource.target_equivalent || resource.service_name || "");
  const meta = TYPE_META[type];
  const Icon = meta.icon;
  const name = resource.target_service || resource.target_equivalent || resource.service_name || "Service";
  const strategy = resource.strategy || resource.seven_r_strategy || resource.migration_strategy;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04 }}
      style={{
        padding: "12px 14px", borderRadius: 9,
        background: "var(--surface-0)", border: "1px solid var(--surface-4)",
        display: "flex", alignItems: "flex-start", gap: 10,
      }}
    >
      <div style={{
        width: 32, height: 32, borderRadius: 8, flexShrink: 0,
        background: `${meta.color}12`, border: `1px solid ${meta.color}30`,
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        <Icon size={15} style={{ color: meta.color }} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)" }}>{name}</div>
        <div style={{ fontSize: 10.5, color: "var(--text-tertiary)", marginTop: 2 }}>
          {meta.label}
          {resource.source_service && (
            <span> · depuis <span style={{ fontFamily: "var(--font-mono)" }}>{resource.source_service}</span></span>
          )}
        </div>
      </div>
      {strategy && (
        <span style={{
          padding: "2px 7px", borderRadius: 8, fontSize: 9.5, fontWeight: 600,
          background: "var(--info-subtle)", color: "var(--info-text)", border: "1px solid var(--info-border)",
          flexShrink: 0,
        }}>
          {strategy}
        </span>
      )}
    </motion.div>
  );
}

export default function IaCGenerator() {
  const [migrations, setMigrations]     = useState([]);
  const [loading, setLoading]           = useState(true);
  const [selected, setSelected]         = useState(null);
  const [detail, setDetail]             = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError]               = useState(null);

  const load = () => {
    setLoading(true);
    fetchMigrations()
      .then(data => {
        setMigrations(data);
        const first = data.find(m => IaC_STATUSES.includes(m.status));
        if (first && !selected) selectMigration(first, data);
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };

  const selectMigration = (m, list = migrations) => {
    setSelected(m.id);
    setDetail(null);
    setDetailLoading(true);
    fetchMigration(m.id)
      .then(setDetail)
      .catch(() => setDetail(list.find(x => x.id === m.id) || m))
      .finally(() => setDetailLoading(false));
  };

  useEffect(load, []);

  const iacMigrations = migrations.filter(m => IaC_STATUSES.includes(m.status));
  const otherMigrations = migrations.filter(m => !IaC_STATUSES.includes(m.status));

  const resources = detail?.migration_plan?.resources || detail?.plan?.resources || [];
  const summary   = detail?.migration_plan?.summary || detail?.plan?.summary || {};
  const selectedM = migrations.find(m => m.id === selected);

  const tc = selectedM ? (CLOUD_COLORS[selectedM.target_cloud?.toLowerCase()] || { color: "#6366F1", label: selectedM.target_cloud }) : null;

  return (
    <motion.div
      className="page"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
      style={{ display: "flex", flexDirection: "column", height: "100%" }}
    >
      <div className="page-header">
        <div>
          <h2 className="page-title">IaC Generator</h2>
          <p className="page-subtitle">
            {loading ? "Chargement…" : `${iacMigrations.length} migration${iacMigrations.length !== 1 ? "s" : ""} avec IaC générée`}
          </p>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
            Rafraîchir
          </button>
          <Link to="/migrations/new" className="btn btn-primary">Nouvelle Migration</Link>
        </div>
      </div>

      {error && (
        <div style={{
          padding: "10px 14px", borderRadius: 8, marginBottom: 16,
          background: "var(--error-subtle)", border: "1px solid var(--error-border)",
          color: "var(--error-text)", fontSize: 13,
        }}>{error}</div>
      )}

      {/* Stats */}
      {!loading && (
        <div className="stats-bar" style={{ gridTemplateColumns: "repeat(3, 1fr)", marginBottom: 20 }}>
          {[
            { icon: FileCode2,   label: "IaC générée",    value: iacMigrations.length,  accent: "blue"   },
            { icon: CheckCircle, label: "Déployées",       value: migrations.filter(m => ["Completed","Exported"].includes(m.status)).length, accent: "green" },
            { icon: Shield,      label: "Validées Checkov",value: migrations.filter(m => ["Completed","Exported","Deploying","Health_Checking"].includes(m.status)).length, accent: "purple" },
          ].map((s, i) => (
            <motion.div key={s.label} className="stat-card"
              initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.05 }}
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

      {/* Two-panel layout */}
      <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
        {/* Left: migration list */}
        <div style={{
          width: 280, flexShrink: 0, display: "flex", flexDirection: "column",
          background: "var(--surface-0)", borderRadius: 12,
          border: "1px solid var(--surface-4)", overflow: "hidden",
        }}>
          <div style={{
            padding: "10px 14px 8px",
            borderBottom: "1px solid var(--surface-3)",
            fontSize: 10, fontWeight: 700, textTransform: "uppercase",
            letterSpacing: "0.1em", color: "var(--text-disabled)",
          }}>
            Avec IaC ({iacMigrations.length})
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "6px 8px", scrollbarWidth: "none" }}>
            {loading
              ? [...Array(3)].map((_, i) => (
                  <div key={i} style={{
                    height: 52, borderRadius: 8, margin: "4px 0",
                    background: "var(--surface-3)", animation: "skeleton-pulse 1.5s ease-in-out infinite",
                  }} />
                ))
              : iacMigrations.length === 0
                ? <div style={{ padding: "20px 12px", fontSize: 12, color: "var(--text-tertiary)", textAlign: "center" }}>
                    Aucune IaC générée pour l'instant.
                  </div>
                : iacMigrations.map(m => (
                    <MigrationRow key={m.id} m={m} selected={selected === m.id} onSelect={() => selectMigration(m)} />
                  ))
            }

            {otherMigrations.length > 0 && !loading && (
              <>
                <div style={{
                  padding: "10px 12px 6px",
                  fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.1em", color: "var(--text-disabled)",
                }}>
                  En cours / autres ({otherMigrations.length})
                </div>
                {otherMigrations.map(m => (
                  <MigrationRow key={m.id} m={m} selected={selected === m.id} onSelect={() => selectMigration(m)} />
                ))}
              </>
            )}
          </div>
        </div>

        {/* Right: IaC detail */}
        <div style={{ flex: 1, minWidth: 0, overflowY: "auto" }}>
          {!selected ? (
            <div style={{
              height: "100%", display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "center",
              color: "var(--text-tertiary)", gap: 12,
            }}>
              <FileCode2 size={40} style={{ opacity: 0.3 }} />
              <p style={{ fontSize: 13 }}>Sélectionnez une migration pour voir son IaC</p>
            </div>
          ) : detailLoading ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {[...Array(5)].map((_, i) => (
                <div key={i} style={{
                  height: 56, borderRadius: 9, background: "var(--surface-3)",
                  animation: "skeleton-pulse 1.5s ease-in-out infinite",
                  animationDelay: `${i * 0.08}s`,
                }} />
              ))}
            </div>
          ) : (
            <AnimatePresence mode="wait">
              <motion.div key={selected} initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.18 }}>
                {/* Header */}
                <div style={{
                  padding: "14px 16px", borderRadius: 12, marginBottom: 16,
                  background: "var(--surface-0)", border: "1px solid var(--surface-4)",
                  display: "flex", alignItems: "center", gap: 12,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 13.5, fontWeight: 700, color: "var(--text-primary)",
                      fontFamily: "var(--font-mono)",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {selectedM?.repo_url.replace("https://github.com/", "")}
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5 }}>
                      <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}>
                        #{selected.slice(0, 8)}
                      </span>
                      {selectedM && (
                        <>
                          <ArrowRight size={11} style={{ color: "var(--text-tertiary)" }} />
                          <span style={{ fontSize: 11, fontWeight: 700, color: tc?.color }}>{tc?.label}</span>
                        </>
                      )}
                      {selectedM?.target_region && (
                        <span style={{ fontSize: 10.5, color: "var(--text-tertiary)" }}>📍 {selectedM.target_region}</span>
                      )}
                    </div>
                  </div>
                  {selectedM && <StatusBadge status={selectedM.status} />}
                  <Link to={`/migrations/${selected}`} className="btn btn-secondary" style={{ fontSize: 12, padding: "6px 12px" }}>
                    <ExternalLink size={13} /> Voir détails
                  </Link>
                </div>

                {/* Summary */}
                {(summary.provider || summary.total_resources || resources.length > 0) && (
                  <div style={{
                    display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginBottom: 16,
                  }}>
                    {[
                      { label: "Ressources IaC", value: resources.length || summary.total_resources || "—", icon: Package, color: "#6366F1" },
                      { label: "Fournisseur", value: (tc?.label || summary.provider || "—"), icon: Cloud, color: tc?.color || "#0284C7" },
                      { label: "Stratégie", value: selectedM?.seven_r_strategy || summary.strategy || "—", icon: ChevronRight, color: "#15803D" },
                    ].map(s => (
                      <div key={s.label} style={{
                        padding: "12px 14px", borderRadius: 10,
                        background: "var(--surface-0)", border: "1px solid var(--surface-4)",
                        display: "flex", alignItems: "center", gap: 10,
                      }}>
                        <div style={{
                          width: 32, height: 32, borderRadius: 8, flexShrink: 0,
                          background: `${s.color}12`, border: `1px solid ${s.color}30`,
                          display: "flex", alignItems: "center", justifyContent: "center",
                        }}>
                          <s.icon size={15} style={{ color: s.color }} />
                        </div>
                        <div>
                          <div style={{ fontSize: 14, fontWeight: 800, color: "var(--text-primary)" }}>{s.value}</div>
                          <div style={{ fontSize: 10.5, color: "var(--text-tertiary)" }}>{s.label}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Resources list */}
                {resources.length > 0 ? (
                  <>
                    <div style={{
                      fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                      letterSpacing: "0.1em", color: "var(--text-disabled)", marginBottom: 10,
                    }}>
                      Ressources générées ({resources.length})
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {resources.map((r, i) => (
                        <ResourceCard key={i} resource={r} index={i} />
                      ))}
                    </div>
                  </>
                ) : (
                  <div style={{
                    padding: "32px 24px", textAlign: "center",
                    background: "var(--surface-0)", borderRadius: 12,
                    border: "1px solid var(--surface-4)",
                    color: "var(--text-tertiary)", fontSize: 13,
                  }}>
                    <FileCode2 size={32} style={{ opacity: 0.3, marginBottom: 10, display: "block", margin: "0 auto 10px" }} />
                    {IaC_STATUSES.includes(selectedM?.status)
                      ? "Plan de migration disponible — consultez les détails complets pour voir les fichiers Terraform."
                      : "L'IaC n'a pas encore été générée pour cette migration. Lancez l'analyse pour commencer."
                    }
                    <div style={{ marginTop: 14 }}>
                      <Link to={`/migrations/${selected}`} className="btn btn-primary" style={{ fontSize: 12 }}>
                        <ExternalLink size={13} /> Ouvrir la migration
                      </Link>
                    </div>
                  </div>
                )}
              </motion.div>
            </AnimatePresence>
          )}
        </div>
      </div>
    </motion.div>
  );
}
