/**
 * MigrationMapGraph.jsx
 *
 * Visualisation de la migration réelle : services source → services cible.
 * Affiche UNIQUEMENT les services de la migration en cours (pas la base RAG complète).
 *
 * Layout :
 *   Colonne gauche  — services source (dependency_graph.services)
 *   Flèches centre  — colorées par stratégie 7R + score
 *   Colonne droite  — services cible (migration_plan.resources)
 */
import { useMemo } from "react";
import {
  ArrowRight, Database, HardDrive, Cpu, Network,
  Shield, Eye, MessageSquare, Search, Bot, Server,
  CheckCircle2, RefreshCw, Wrench, Archive, Pause, ShoppingCart,
} from "lucide-react";

// ── Couleurs par stratégie 7R ─────────────────────────────────────────────────
const STRATEGY_COLORS = {
  REHOST:     { bg: "#dcfce7", border: "#22c55e", text: "#15803d", arrow: "#22c55e", label: "Rehost" },
  REPLATFORM: { bg: "#fef9c3", border: "#eab308", text: "#854d0e", arrow: "#eab308", label: "Replateforme" },
  REFACTOR:   { bg: "#ffedd5", border: "#f97316", text: "#9a3412", arrow: "#f97316", label: "Refactorisation" },
  RETIRE:     { bg: "#f1f5f9", border: "#94a3b8", text: "#64748b", arrow: "#94a3b8", label: "Abandon" },
  RETAIN:     { bg: "#f0f9ff", border: "#38bdf8", text: "#0369a1", arrow: "#38bdf8", label: "Conservation" },
  REPURCHASE: { bg: "#faf5ff", border: "#a855f7", text: "#7e22ce", arrow: "#a855f7", label: "Remplacement SaaS" },
  RELOCATE:   { bg: "#ecfdf5", border: "#10b981", text: "#065f46", arrow: "#10b981", label: "Relocalisation" },
};

const STRATEGY_ICONS = {
  REHOST: CheckCircle2,
  REPLATFORM: RefreshCw,
  REFACTOR: Wrench,
  RETIRE: Archive,
  RETAIN: Pause,
  REPURCHASE: ShoppingCart,
};

// ── Couleurs par cloud provider ───────────────────────────────────────────────
const CLOUD_COLORS = {
  aws:   { bg: "#fff7ed", border: "#f97316", header: "#ea580c", dot: "#f97316" },
  gcp:   { bg: "#eff6ff", border: "#3b82f6", header: "#2563eb", dot: "#3b82f6" },
  azure: { bg: "#f0fdf4", border: "#22c55e", header: "#16a34a", dot: "#22c55e" },
};
const CLOUD_LABELS = { aws: "Amazon Web Services", gcp: "Google Cloud Platform", azure: "Microsoft Azure" };

// ── Icônes par type de service ────────────────────────────────────────────────
const TYPE_ICONS = {
  storage:    HardDrive,
  database:   Database,
  compute:    Cpu,
  network:    Network,
  networking: Network,
  iam:        Shield,
  monitoring: Eye,
  messaging:  MessageSquare,
  search:     Search,
  ai:         Bot,
  ml:         Bot,
  framework:  Server,
};

function ServiceIcon({ type, size = 14, color = "#64748b" }) {
  const Icon = TYPE_ICONS[type?.toLowerCase()] || Server;
  return <Icon size={size} color={color} />;
}

// ── Inférer le type depuis le nom du service ──────────────────────────────────
function inferType(name = "") {
  const n = name.toLowerCase();
  if (/storage|blob|s3|gcs|bucket|file/.test(n)) return "storage";
  if (/database|db|sql|cosmos|dynamo|firestore|redis|mongo|postgres|mysql|bigtable|spanner/.test(n)) return "database";
  if (/compute|vm|instance|ec2|function|lambda|run|app|aks|eks|gke|kubernetes|container/.test(n)) return "compute";
  if (/network|vpc|vnet|subnet|dns|cdn|gateway|load.?balance|firewall/.test(n)) return "network";
  if (/iam|identity|role|policy|managed.?identity|service.?account|keyvault|secret|cognito/.test(n)) return "iam";
  if (/monitor|alert|log|insight|metric|cloudwatch|stackdriver/.test(n)) return "monitoring";
  if (/queue|bus|event|pubsub|sqs|sns|kinesis|kafka/.test(n)) return "messaging";
  if (/search|opensearch|elastic/.test(n)) return "search";
  if (/ai|ml|model|bert|gpt|bedrock|vertex|openai|sagemaker|cognitive/.test(n)) return "ai";
  return "compute";
}

// ── Composant : carte service ─────────────────────────────────────────────────
function ServiceCard({ name, type, cloud, isTarget = false, score, strategy }) {
  const cloudKey = (cloud || "aws").toLowerCase().replace("azurerm", "azure").replace("google", "gcp");
  const colors = CLOUD_COLORS[cloudKey] || CLOUD_COLORS.aws;
  const inferredType = type || inferType(name);

  // Nom court
  const shortName = name.replace(/^(azurerm_|aws_|google_)/, "").replace(/_/g, " ");

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "8px 12px",
      background: colors.bg,
      border: `1.5px solid ${colors.border}`,
      borderRadius: 8,
      fontSize: 12, fontWeight: 500, color: "#1e293b",
      minWidth: 170, maxWidth: 200,
      position: "relative",
    }}>
      <ServiceIcon type={inferredType} size={14} color={colors.header} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, fontSize: 11, color: colors.header, textTransform: "uppercase", letterSpacing: "0.03em" }}>
          {inferredType}
        </div>
        <div style={{ fontSize: 12, color: "#1e293b", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {shortName}
        </div>
      </div>
      {isTarget && score != null && (
        <div style={{
          position: "absolute", top: -8, right: 6,
          background: score >= 0.85 ? "#22c55e" : score >= 0.70 ? "#eab308" : "#f97316",
          color: "#fff", borderRadius: 10, padding: "1px 6px", fontSize: 10, fontWeight: 700,
        }}>
          {Math.round(score * 100)}%
        </div>
      )}
    </div>
  );
}

// ── Composant : flèche de stratégie ──────────────────────────────────────────
function StrategyArrow({ strategy, score }) {
  const st = STRATEGY_COLORS[strategy] || STRATEGY_COLORS.REPLATFORM;
  const Icon = STRATEGY_ICONS[strategy] || ArrowRight;
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      gap: 2, minWidth: 110,
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 4,
        background: st.bg, border: `1px solid ${st.border}`,
        borderRadius: 12, padding: "3px 10px",
        color: st.text, fontSize: 10, fontWeight: 700,
      }}>
        <Icon size={11} />
        {st.label}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 2 }}>
        <div style={{ width: 35, height: 1.5, background: st.arrow }} />
        <div style={{
          width: 0, height: 0,
          borderTop: "5px solid transparent",
          borderBottom: "5px solid transparent",
          borderLeft: `7px solid ${st.arrow}`,
        }} />
      </div>
    </div>
  );
}

// ── Légende ───────────────────────────────────────────────────────────────────
function Legend() {
  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center",
      padding: "10px 16px",
      background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0",
    }}>
      {Object.entries(STRATEGY_COLORS).map(([key, st]) => {
        const Icon = STRATEGY_ICONS[key] || ArrowRight;
        return (
          <div key={key} style={{
            display: "flex", alignItems: "center", gap: 5,
            padding: "3px 10px",
            background: st.bg, border: `1px solid ${st.border}`,
            borderRadius: 20, color: st.text, fontSize: 11, fontWeight: 600,
          }}>
            <Icon size={11} />
            {st.label}
          </div>
        );
      })}
    </div>
  );
}

// ── Composant principal ───────────────────────────────────────────────────────
export default function MigrationMapGraph({ graph, plan }) {
  // Construire le mapping source → cible depuis migration_plan
  const mapping = useMemo(() => {
    if (!plan?.resources) return [];
    return plan.resources.map((r) => {
      const rawScore = r.composite_score ?? r.score ?? r.equivalence_score ?? null;
      const score = (rawScore != null && rawScore > 0) ? rawScore : null;
      return {
        source: r.source_service || r.resource_name || r.service || "",
        target: r.target_service || r.target_equivalent || r.terraform_resource || "",
        strategy: (r.strategy || r.strategy_7r || "REPLATFORM").toUpperCase(),
        score,
        type: r.type || r.category || inferType(r.source_service || ""),
      };
    });
  }, [plan]);

  const sourceCloud = graph?.detected_cloud || "aws";
  const targetCloud = (plan?.target_cloud || "azure").toLowerCase().replace("azurerm", "azure");

  // Services source depuis dependency_graph
  const sourceServices = useMemo(() => {
    if (!graph?.services) return [];
    return graph.services.map((s) => ({
      name: s.service || s.resource_name || s.name || "",
      type: s.type || inferType(s.service || ""),
    }));
  }, [graph]);

  if (mapping.length === 0 && sourceServices.length === 0) {
    return (
      <div style={{
        padding: 40, textAlign: "center", color: "#94a3b8",
        background: "#f8fafc", borderRadius: 12, border: "1px dashed #cbd5e1",
      }}>
        <Server size={32} color="#cbd5e1" style={{ margin: "0 auto 12px" }} />
        <p style={{ margin: 0, fontSize: 14 }}>
          Aucune donnée de migration disponible.<br />
          Le plan de migration doit être généré pour afficher ce graphe.
        </p>
      </div>
    );
  }

  const cloudSrcLabel = CLOUD_LABELS[sourceCloud] || sourceCloud.toUpperCase();
  const cloudTgtLabel = CLOUD_LABELS[targetCloud] || targetCloud.toUpperCase();
  const cloudSrcColors = CLOUD_COLORS[sourceCloud] || CLOUD_COLORS.aws;
  const cloudTgtColors = CLOUD_COLORS[targetCloud] || CLOUD_COLORS.azure;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* En-tête */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "12px 16px", background: "#f8fafc",
        borderRadius: 10, border: "1px solid #e2e8f0",
      }}>
        <div style={{ flex: 1, textAlign: "center" }}>
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "4px 14px", borderRadius: 20,
            background: cloudSrcColors.bg, border: `1.5px solid ${cloudSrcColors.border}`,
            color: cloudSrcColors.header, fontWeight: 700, fontSize: 13,
          }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: cloudSrcColors.dot }} />
            {cloudSrcLabel}
          </div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>Cloud source</div>
        </div>
        <ArrowRight size={22} color="#94a3b8" />
        <div style={{ flex: 1, textAlign: "center" }}>
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            padding: "4px 14px", borderRadius: 20,
            background: cloudTgtColors.bg, border: `1.5px solid ${cloudTgtColors.border}`,
            color: cloudTgtColors.header, fontWeight: 700, fontSize: 13,
          }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: cloudTgtColors.dot }} />
            {cloudTgtLabel}
          </div>
          <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>Cloud cible</div>
        </div>
      </div>

      {/* Légende */}
      <Legend />

      {/* Tableau de mapping */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {mapping.length > 0 ? (
          mapping.map((row, i) => {
            const isRetired = ["RETIRE", "RETAIN"].includes(row.strategy);
            return (
              <div key={i} style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 12px",
                background: "#fff",
                border: "1px solid #f1f5f9",
                borderRadius: 10,
                opacity: isRetired ? 0.65 : 1,
              }}>
                {/* Service source */}
                <ServiceCard
                  name={row.source}
                  type={row.type}
                  cloud={sourceCloud}
                />
                {/* Flèche stratégie */}
                <StrategyArrow strategy={row.strategy} score={row.score} />
                {/* Service cible */}
                {!isRetired ? (
                  <ServiceCard
                    name={row.target || "—"}
                    type={inferType(row.target)}
                    cloud={targetCloud}
                    isTarget
                    score={row.score}
                    strategy={row.strategy}
                  />
                ) : (
                  <div style={{
                    padding: "8px 12px",
                    background: "#f8fafc", border: "1px dashed #cbd5e1",
                    borderRadius: 8, fontSize: 12, color: "#94a3b8", fontStyle: "italic",
                    minWidth: 170,
                  }}>
                    {row.strategy === "RETIRE" ? "Abandonné" : "Conservé (source)"}
                  </div>
                )}
              </div>
            );
          })
        ) : (
          /* Si pas de plan mais des services source : afficher les services source seuls */
          sourceServices.map((svc, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "8px 12px",
              background: "#fff", border: "1px solid #f1f5f9", borderRadius: 10,
            }}>
              <ServiceCard name={svc.name} type={svc.type} cloud={sourceCloud} />
              <div style={{ color: "#cbd5e1", fontSize: 12 }}>— plan en attente</div>
            </div>
          ))
        )}
      </div>

      {/* Statistiques */}
      {mapping.length > 0 && (
        <div style={{
          display: "flex", gap: 12, flexWrap: "wrap",
          padding: "10px 16px",
          background: "#f8fafc", borderRadius: 8, border: "1px solid #e2e8f0",
          fontSize: 12,
        }}>
          {Object.entries(
            mapping.reduce((acc, r) => {
              acc[r.strategy] = (acc[r.strategy] || 0) + 1;
              return acc;
            }, {})
          ).map(([strat, count]) => {
            const st = STRATEGY_COLORS[strat] || STRATEGY_COLORS.REPLATFORM;
            return (
              <div key={strat} style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "2px 10px",
                background: st.bg, border: `1px solid ${st.border}`,
                borderRadius: 20, color: st.text, fontWeight: 600,
              }}>
                {count} × {st.label}
              </div>
            );
          })}
          {mapping.some((r) => r.score != null) && (
            <div style={{ color: "#64748b", marginLeft: "auto" }}>
              Score moyen :{" "}
              <strong>
                {Math.round(
                  (mapping.reduce((a, r) => a + (r.score || 0), 0) / mapping.filter((r) => r.score != null).length) * 100
                )}%
              </strong>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
