import { useState, useEffect, useCallback, useRef } from "react";
import { useSSE, ACTIVE_STATUSES } from "../hooks/useSSE";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Rocket,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Cloud,
  ArrowRight,
  Clock,
  FileText,
  GitBranch,
  Download,
  Trash2,
  Cpu,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Loader2,
  Sparkles,
  X as XIcon,
} from "lucide-react";
import {
  fetchMigration,
  startAnalysis,
  acceptPlan,
  rejectPlan,
  partialRejectPlan,
  deleteMigration,
  submitServices,
  retryValidation,
} from "../api/migrationApi";
import StatusBadge from "../components/common/StatusBadge";
import Spinner from "../components/common/Spinner";
import Alert from "../components/common/Alert";
import MigrationPriority from "../components/Migration/MigrationPriority";
import PreviewReport from "../components/Migration/PreviewReport";
import AIStackSection from "../components/Migration/AIStackSection";
import StepTimeline from "../components/Migration/StepTimeline";
import PipelineCICD, { CICDExecutionPanel, CICDPipelineDelivered } from "../components/Migration/PipelineCICD";
import TerraformFilesPanel from "../components/Migration/TerraformFilesPanel";
import ServiceSelectionChecklist from "../components/Migration/ServiceSelectionChecklist";
import GraphSwitcher from "../components/Graph/GraphSwitcher";
import DeploymentPanel from "../components/Migration/DeploymentPanel";
import SecurityPanel from "../components/Migration/SecurityPanel";
import InfraHealthPanel from "../components/Migration/InfraHealthPanel";
import RAGChatbot from "../components/Migration/RAGChatbot";
import WarningsBanner from "../components/Migration/WarningsBanner";
import TerraformLogsStream from "../components/Migration/TerraformLogsStream";
import KnowledgeGraphPanel from "../components/Migration/KnowledgeGraphPanel";
import { printRejectionReport } from "../utils/printReport";
import { useI18n } from "../context/I18nContext";

export default function MigrationDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { t, lang } = useI18n();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const [tab, setTab] = useState("pipeline");
  const [chatOpen, setChatOpen] = useState(false);

  // SSE live logs state
  const [showLogs, setShowLogs]   = useState(false);
  const [sseLog,   setSseLog]     = useState([]);  // [{phase, label, ts, type}]

  // Partial rejection state
  const [showPartialReject, setShowPartialReject] = useState(false);
  const [selectedServices, setSelectedServices] = useState({});   // { service_name: bool }
  const [rejectionReasons, setRejectionReasons] = useState({});   // { service_name: reason }
  const [humanInputBanner, setHumanInputBanner] = useState(false);
  const [showServiceSelection, setShowServiceSelection] = useState(false);
  const [serviceSelectionLoading, setServiceSelectionLoading] = useState(false);
  const actionBarRef = useRef(null);

  const load = useCallback(() => {
    fetchMigration(id)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Real-time updates via SSE (auto-reconnect via EventSource, phase labels)
  const { connected: wsConnected, lastEvent: wsLastEvent } = useSSE(
    id,
    data?.status ?? null,
    load,
  );

  // Polling fallback: a fresh EventSource only delivers events emitted AFTER
  // it connects (no replay of history). If the page is loaded/refreshed mid-
  // pipeline, or the SSE stream silently drops, `data` can stay stuck on a
  // stale status forever — the Pipeline view then shows phases as "EN COURS"
  // long after the backend has finished them. Poll while the migration is
  // active so `data.status`/`artifacts` always converge to backend reality,
  // independent of SSE health.
  useEffect(() => {
    if (!id || !ACTIVE_STATUSES.has(data?.status)) return;
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, [id, data?.status, load]);

  // Accumulate SSE events into live log + handle human_input_required
  useEffect(() => {
    if (!wsLastEvent) return;
    if (wsLastEvent.event_type === "human_input_required") {
      setHumanInputBanner(true);
      setTimeout(() => {
        actionBarRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 300);
    }
    if (wsLastEvent.event_type === "service_selection_required") {
      setShowServiceSelection(true);
    }
    // Re-fetch migration data whenever a phase completes so artifacts
    // (terraform_files, iac_validation/checkov, github_pr_url, health_report…)
    // are reflected as soon as they're persisted — not just at the very end.
    // Without this, `data.artifacts` stays stale (e.g. empty terraform_files)
    // through agent_02/agent_03, and the UI falls back to placeholder content.
    const refetchPhases = ["agent_02", "validate_iac", "agent_02_fix", "agent_03", "publish_github", "health_check", "export_zip"];
    if (
      wsLastEvent.event_type === "phase_completed" &&
      refetchPhases.includes(wsLastEvent.phase)
    ) {
      setTimeout(load, 800);
      if (wsLastEvent.phase === "export_zip") {
        setSuccess("Pipeline terminé avec succès — artefacts disponibles.");
      }
    }
    // Append to live log
    setSseLog((prev) => {
      const now = Date.now();
      const entry = {
        id:    now + Math.random(),
        rawTs: now,
        ts:    new Date().toLocaleTimeString(lang === "fr" ? "fr-FR" : "en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
        type:  wsLastEvent.event_type,
        phase: wsLastEvent.phase || "",
        label: wsLastEvent.phase_label || wsLastEvent.message || wsLastEvent.event_type,
        error: wsLastEvent.error,
      };
      const updated = [...prev, entry];
      return updated.length > 100 ? updated.slice(-100) : updated;
    });
  }, [wsLastEvent]);

  const handleFullReject = async () => {
    setActionLoading("Rejet");
    setError(null);
    setSuccess(null);
    try {
      const result = await rejectPlan(id);
      setData((prev) => ({ ...prev, ...result }));
      setSuccess("Plan rejeté — téléchargement du rapport en cours...");
      // Build export bundle from current state data
      const exportBundle = {
        migration_id: data.id,
        repo_url: data.repo_url,
        source_cloud: data.source_cloud,
        target_cloud: data.target_cloud,
        exported_at: new Date().toISOString(),
        migration_plan: data.migration_plan || null,
        preview_report: data.preview_report || null,
        dependency_graph: data.dependency_graph || null,
      };

      // ── JSON export (machine-readable) ──────────────────────────────────
      const blob = new Blob(
        [JSON.stringify(exportBundle, null, 2)],
        { type: "application/json" }
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `migration-${data.id}-export.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      // ── PDF export (human-readable report) ──────────────────────────────
      printRejectionReport(exportBundle);
      setTimeout(load, 1000);
    } catch (e) {
      setError(e.message);
    } finally {
      setActionLoading(null);
    }
  };

  const doAction = async (label, fn, successMsg = null) => {
    setActionLoading(label);
    setError(null);
    setSuccess(null);
    try {
      const result = await fn();
      setData((prev) => ({ ...prev, ...result }));
      if (successMsg) setSuccess(successMsg);
      setTimeout(load, 1000);
    } catch (e) {
      // Translate structured missing_credentials error into actionable UI text.
      let msg = e.message;
      try {
        const parsed = JSON.parse(e.message);
        if (parsed?.error === "missing_credentials" && Array.isArray(parsed.missing)) {
          const providers = parsed.missing
            .map((p) => p.toUpperCase())
            .join(", ");
          msg =
            `${providers} credentials manquants pour cette migration. ` +
            `Rendez-vous sur la page de création et utilisez POST /credentials/store ` +
            `(provider="${parsed.missing[0]}") avant d'accepter le plan.`;
        }
      } catch (_) {
        // not JSON — use raw message as-is
      }
      setError(msg);
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async () => {
    if (!confirm(t("detail.delete.confirm"))) return;
    try {
      await deleteMigration(id);
      navigate("/dashboard");
    } catch (e) {
      setError(e.message);
    }
  };

  const handleSubmitServices = async (services) => {
    setServiceSelectionLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await submitServices(id, services);
      const cleanResult = {
        ...result,
        dependency_graph: result?.dependency_graph
          ? { ...result.dependency_graph, needs_service_selection: false }
          : result?.dependency_graph,
      };
      setData((prev) => ({ ...prev, ...cleanResult }));
      setShowServiceSelection(false);
      setSuccess("Services soumis — génération du plan de migration en cours…");
      setTimeout(load, 1000);
    } catch (e) {
      setError(e.message);
    } finally {
      setServiceSelectionLoading(false);
    }
  };

  const handlePartialReject = async () => {
    const plan = data?.migration_plan;
    if (!plan) return;

    // Index-based selection: keys are resource indices
    const rejectedIndices = Object.entries(selectedServices)
      .filter(([, checked]) => checked)
      .map(([idx]) => parseInt(idx, 10));

    if (rejectedIndices.length === 0) {
      setError("Veuillez selectionner au moins un service a rejeter.");
      return;
    }

    // Build rejectedServices list from plan resources by index
    const resources = plan.resources || [];
    const rejectedServices = rejectedIndices
      .map((idx) => resources[idx])
      .filter(Boolean);

    // Build reasons map { source_service: reason }
    const reasons = {};
    rejectedIndices.forEach((idx) => {
      const svc = resources[idx];
      if (svc && rejectionReasons[idx]) {
        const key = svc.source_service || svc.resource_name || svc.service_name || svc.name || `service-${idx}`;
        reasons[key] = rejectionReasons[idx];
      }
    });

    setActionLoading("RejetPartiel");
    setError(null);
    setSuccess(null);
    try {
      const result = await partialRejectPlan(id, rejectedServices, reasons);
      setData((prev) => ({ ...prev, ...result }));
      setSuccess(t("detail.correction.done").replace("{n}", rejectedServices.length));
      setShowPartialReject(false);
      setSelectedServices({});
      setRejectionReasons({});
      setTimeout(load, 1000);
    } catch (e) {
      setError(e.message);
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) return <Spinner size={32} text={t("dashboard.loading")} />;
  if (!data) return <Alert type="error">{t("detail.not.found")}</Alert>;

  const graph = data.dependency_graph;
  const plan = data.migration_plan;
  const report = data.preview_report;
  const isPlanReady = data.status === "Plan_Ready" || data.status === "Reviewing";
  const canAnalyze = data.status === "Created";

  // Services détectés comme connexions externes (RETAIN automatique par le stack analyzer)
  // source = "env_var_pattern" ou "manifest" + pas de fichier IaC déclarant la ressource
  const retainedExternalServices = (plan?.services || []).filter(svc => {
    const strategy = (svc.strategy_7r || svc.strategy || "").toUpperCase();
    const reasoning = (svc.reasoning || "").toLowerCase();
    return strategy === "RETAIN" && (
      reasoning.includes("external connection") ||
      reasoning.includes("connexion externe") ||
      reasoning.includes("env_var") ||
      reasoning.includes("connectivity only")
    );
  });

  const healthReport    = data.artifacts?.health_report;
  const intentIssues    = data.artifacts?.intent_issues || [];
  const tfFiles         = data.artifacts?.terraform_files || [];
  const patchedPyFiles  = data.artifacts?.patched_python_files || [];
  const sdkChanges      = data.artifacts?.sdk_changes_applied || {};
  const regionNotices   = data.artifacts?.region_notices || [];
  const needsEscalation = data.artifacts?.needs_human_escalation || false;
  // github_pr_url / github_repo_url: read from multiple sources in priority order:
  //   1. top-level DB columns (set by accept_plan after pipeline, migration 019)
  //   2. artifacts.github_pr_url (set by publish_github_node in LangGraph state)
  //   3. artifacts.github_delivery (set by deliver_artifacts_to_github fallback)
  const githubPrUrl = (
    data.github_pr_url ||
    data.artifacts?.github_pr_url ||
    data.artifacts?.github_delivery?.pr_url ||
    ""
  );
  const githubRepoUrl = (
    data.github_repo_url ||
    data.artifacts?.github_repo_url ||
    data.artifacts?.github_delivery?.repo_url ||
    ""
  );
  const iacValidation   = data.artifacts?.iac_validation || null;
  const blockingSecFailures = (iacValidation?.security_scan?.failed_checks || [])
    .filter(c => ["CRITICAL","HIGH"].includes((c.severity || "").toUpperCase())).length;

  const hasDeployment = ["Deploying", "Health_Checking", "Completed", "Exported"].includes(data.status)
    || !!data.artifacts?.runner_job_id || !!data.deployment_status;

  // Path 2 — incremental migration info
  const migrationMode     = data.migration_mode || "full";
  const detectedChanges   = data.detected_changes || null;
  const isIncremental     = migrationMode === "incremental";

  const tabs = [
    { id: "pipeline",       label: "Pipeline" },
    { id: "overview",       label: t("detail.tab.overview") },
    { id: "infrastructure", label: "Infrastructure",           show: !!(graph || plan) },
    { id: "ai-stack",       label: "AI Stack",                 show: !!graph },
    { id: "plan",           label: t("detail.tab.plan"),       show: !!plan },
    { id: "report",         label: t("detail.tab.report"),     show: !!report },
    { id: "knowledge-graph",label: "🕸 Knowledge Graph",       show: true },
    { id: "terraform",      label: `Terraform (${tfFiles.length})`, show: tfFiles.length > 0 || needsEscalation },
    { id: "python-migrated", label: `🐍 Code Python (${patchedPyFiles.length})`, show: patchedPyFiles.length > 0 },
    { id: "security",       label: blockingSecFailures > 0 ? `⚠ ${t("detail.tab.security")} (${blockingSecFailures})` : t("detail.tab.security"), show: !!iacValidation },
    { id: "deploy-cicd",    label: `🚀 ${t("detail.tab.deploy")}`,   show: true },
    { id: "cicd-delivered", label: "🔁 Pipeline CI/CD livré",        show: !!githubRepoUrl },
    { id: "infra-monitor",  label: "🟢 Infra Monitor",         show: hasDeployment },
    { id: "health",         label: "Health Report",            show: !!healthReport },
  ].filter((t) => t.show !== false);

  return (
    <div className="page">
      {/* Header */}
      <div className="page-header">
        <div>
          <button className="btn btn-ghost btn-sm" onClick={() => navigate("/dashboard")}>
            <ArrowLeft size={16} /> Dashboard
          </button>
          <h2 className="page-title" style={{ marginTop: 8 }}>
            {data.repo_url.replace("https://github.com/", "")}
          </h2>
          <div className="detail-meta">
            <StatusBadge status={data.status} />
            <span className="meta-id">ID: {data.id}</span>
            <span className="meta-clouds">
              <Cloud size={14} /> {data.source_cloud?.toUpperCase()} <ArrowRight size={12} />{" "}
              {data.target_cloud?.toUpperCase()}
            </span>
            {data.target_region && (
              <span className="meta-region" style={{ fontSize: 12, color: "var(--muted)" }}>
                📍 {data.target_region}
              </span>
            )}
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-ghost" onClick={load}>
            <RefreshCw size={16} />
          </button>
          <button className="btn btn-ghost danger" onClick={handleDelete}>
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      {error && <Alert type="error" onClose={() => setError(null)}>{error}</Alert>}
      {success && <Alert type="success" onClose={() => setSuccess(null)}>{success}</Alert>}
      {data.errors?.length > 0 && data.errors.map((e, i) => (
        <Alert key={i} type="warning">{e}</Alert>
      ))}

      {/* Bannière services externes détectés automatiquement par le stack analyzer */}
      {retainedExternalServices.length > 0 && (
        <div style={{
          display: "flex", alignItems: "flex-start", gap: 12,
          padding: "12px 18px", marginBottom: 12,
          background: "#f8fafc", border: "1px solid #94a3b8", borderRadius: 8,
          fontSize: 13, color: "#334155",
        }}>
          <span style={{ fontSize: 18, flexShrink: 0 }}>🔍</span>
          <div>
            <strong style={{ display: "block", marginBottom: 4 }}>
              {retainedExternalServices.length} service{retainedExternalServices.length > 1 ? "s détectés" : " détecté"} comme connexion externe
            </strong>
            <span style={{ color: "#64748b", lineHeight: 1.5 }}>
              Le stack analyzer a détecté{" "}
              <strong>{retainedExternalServices.map(s => s.service_name || s.service).join(", ")}</strong>{" "}
              dans le code source (variables d'environnement, imports) mais sans fichier IaC
              (Terraform/Bicep) qui déclare ces ressources dans ce repo.
              Ces services sont conservés en l'état (<strong>RETAIN</strong>) — l'application
              se connecte à eux mais ne les gère pas. Aucune action requise.
            </span>
          </div>
        </div>
      )}

      {/* GitHub repo banner — shown as soon as repo URL is available, with or without PR */}
      {(githubPrUrl || githubRepoUrl) && (
        <div style={{
          display: "flex", alignItems: "center", gap: 12,
          padding: "12px 18px", marginBottom: 12,
          background: "#f0fdf4", border: "1px solid #22c55e", borderRadius: 8,
          fontSize: 13, fontWeight: 600, color: "#15803d",
        }}>
          <GitBranch size={16} color="#22c55e" />
          <span>
            {githubPrUrl
              ? "Pull Request ouverte — vos fichiers Terraform sont prêts pour review."
              : "Repo GitHub créé — vos fichiers IaC sont disponibles."}
          </span>
          {githubRepoUrl && (
            <a
              href={githubRepoUrl}
              target="_blank"
              rel="noreferrer"
              style={{
                marginLeft: "auto", padding: "4px 14px",
                background: "#fff", color: "#15803d",
                border: "1px solid #22c55e",
                borderRadius: 6, fontSize: 12, fontWeight: 700,
                textDecoration: "none", whiteSpace: "nowrap",
                display: "flex", alignItems: "center", gap: 4,
              }}
            >
              <GitBranch size={12} /> Voir le repo →
            </a>
          )}
          {githubPrUrl && (
            <a
              href={githubPrUrl}
              target="_blank"
              rel="noreferrer"
              style={{
                padding: "4px 14px",
                background: "#22c55e", color: "#fff",
                borderRadius: 6, fontSize: 12, fontWeight: 700,
                textDecoration: "none", whiteSpace: "nowrap",
              }}
            >
              Voir la PR →
            </a>
          )}
        </div>
      )}

      {/* Human input required banner */}
      {humanInputBanner && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "12px 18px", marginBottom: 12,
          background: "#fffbeb", border: "1px solid #f59e0b", borderRadius: 8,
          fontSize: 13, fontWeight: 600, color: "#92400e",
        }}>
          <AlertTriangle size={16} color="#f59e0b" />
          Décision requise — le pipeline attend votre validation du plan de migration.
          <button
            style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "#92400e", fontSize: 18, lineHeight: 1 }}
            onClick={() => setHumanInputBanner(false)}
          >×</button>
        </div>
      )}

      {/* Service selection modal (no-IaC path) — rendered as portal-style overlay */}
      {(showServiceSelection || data?.dependency_graph?.needs_service_selection === true) && (
        <ServiceSelectionChecklist
          sourceCloud={data.source_cloud}
          onSubmit={handleSubmitServices}
          loading={serviceSelectionLoading}
          submitError={error}
          detectedServices={data.dependency_graph?.python_ai_stack?.sdk_clients || []}
          lang={lang}
        />
      )}

      {/* Action buttons */}
      <div className="action-bar" ref={actionBarRef}>
        {canAnalyze && (
          <button
            className="btn btn-primary btn-lg"
            disabled={!!actionLoading}
            onClick={() => {
              // Set status to Analyzing immediately so useSSE opens before the
              // POST returns — otherwise service_selection_required is missed.
              setData((prev) => prev ? { ...prev, status: "Analyzing" } : prev);
              doAction("Pipeline", () => startAnalysis(id), null);
            }}
          >
            {actionLoading === "Pipeline" ? <Spinner size={16} /> : <Rocket size={18} />}
            Lancer le Pipeline
          </button>
        )}

        {isPlanReady && (
          <>
            <button
              className="btn btn-success btn-lg"
              disabled={!!actionLoading}
              onClick={() => doAction("Acceptation", () => acceptPlan(id), "Plan accepté — génération IaC en cours…")}
            >
              {actionLoading === "Acceptation" ? <Spinner size={16} /> : <CheckCircle2 size={18} />}
              Accepter le plan
            </button>
            <button
              className="btn btn-warning btn-lg"
              disabled={!!actionLoading}
              onClick={() => setShowPartialReject((v) => !v)}
            >
              <AlertTriangle size={18} />
              Rejet partiel
              {showPartialReject ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            <button
              className="btn btn-danger btn-lg"
              disabled={!!actionLoading}
              onClick={handleFullReject}
            >
              {actionLoading === "Rejet" ? <Spinner size={16} /> : <XCircle size={18} />}
              Rejeter (export ZIP)
            </button>
          </>
        )}
      </div>

      {/* Partial rejection panel */}
      {isPlanReady && showPartialReject && plan && (
        <PartialRejectPanel
          resources={plan.resources || []}
          selectedServices={selectedServices}
          rejectionReasons={rejectionReasons}
          onToggle={(name) =>
            setSelectedServices((s) => ({ ...s, [name]: !s[name] }))
          }
          onReason={(name, reason) =>
            setRejectionReasons((r) => ({ ...r, [name]: reason }))
          }
          onSubmit={handlePartialReject}
          onCancel={() => {
            setShowPartialReject(false);
            setSelectedServices({});
            setRejectionReasons({});
          }}
          loading={actionLoading === "RejetPartiel"}
        />
      )}

      {/* Security blocking failures banner */}
      {blockingSecFailures > 0 && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "11px 16px", marginBottom: 10,
          background: "#fef2f2", border: "1px solid #fca5a5", borderRadius: 8,
          fontSize: 13, color: "#991b1b",
        }}>
          <AlertTriangle size={16} color="#dc2626" style={{ flexShrink: 0 }} />
          <span>
            <strong>{blockingSecFailures} faille(s) de sécurité bloquante(s)</strong> détectée(s) dans le code Terraform (CRITICAL/HIGH).
          </span>
          <button
            onClick={() => setTab("security")}
            style={{
              marginLeft: "auto", padding: "4px 12px",
              background: "#dc2626", color: "#fff",
              border: "none", borderRadius: 6, cursor: "pointer",
              fontSize: 11, fontWeight: 700,
            }}
          >
            Voir les détails →
          </button>
        </div>
      )}

      {/* Completed deployment banner — vérification du statut réel du runner */}
      {data.status === "Completed" && (() => {
        const deployOk = data.deployment_status === "deployed";
        const deployFailed = data.deployment_status === "failed" || data.deployment_status === "blocked";
        const scriptOnly = !data.deployment_status || data.deployment_status === "failed_to_enqueue";

        if (deployFailed) {
          return (
            <div style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "12px 18px", marginBottom: 12,
              background: "linear-gradient(135deg, #fef2f2, #fee2e2)",
              border: "1px solid #fca5a5", borderRadius: 8,
              fontSize: 13, fontWeight: 600, color: "#b91c1c",
            }}>
              <XCircle size={18} color="#ef4444" />
              Échec du déploiement cloud — terraform apply a échoué.
              Vérifiez l'onglet CI/CD &amp; Terraform pour les détails.
              {data.target_region && (
                <span style={{ marginLeft: 4, fontWeight: 400, fontSize: 12 }}>
                  📍 {data.target_region}
                </span>
              )}
            </div>
          );
        }

        if (scriptOnly) {
          return (
            <div style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "12px 18px", marginBottom: 12,
              background: "linear-gradient(135deg, #fefce8, #fef9c3)",
              border: "1px solid #fde047", borderRadius: 8,
              fontSize: 13, fontWeight: 600, color: "#a16207",
            }}>
              <FileText size={18} color="#ca8a04" />
              Scripts IaC générés — déploiement manuel requis via CI/CD ou deploy.sh.
            </div>
          );
        }

        if (deployOk) {
          return (
            <div style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "12px 18px", marginBottom: 12,
              background: "linear-gradient(135deg, #f0fdf4, #dcfce7)",
              border: "1px solid #86efac", borderRadius: 8,
              fontSize: 13, fontWeight: 600, color: "#15803d",
            }}>
              <CheckCircle2 size={18} color="#22c55e" />
              Infrastructure déployée avec succès dans le cloud cible.
              {data.target_region && (
                <span style={{ marginLeft: 4, fontWeight: 400, fontSize: 12 }}>
                  📍 {data.target_region}
                </span>
              )}
            </div>
          );
        }

        return null;
      })()}

      {/* Path 2 — incremental migration banner */}
      {isIncremental && detectedChanges && (
        <div style={{
          marginBottom: 12, padding: "12px 16px",
          background: "linear-gradient(135deg, rgba(99,102,241,0.07), rgba(14,165,233,0.07))",
          border: "1px solid rgba(99,102,241,0.2)", borderRadius: 10,
          display: "flex", alignItems: "flex-start", gap: 12,
        }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8, flexShrink: 0,
            background: "linear-gradient(135deg,#6366f1,#0ea5e9)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 14,
          }}>⚡</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 700, color: "#4f46e5", marginBottom: 4 }}>
              Migration incrémentale — Seuls les changements détectés sont traités
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6 }}>
              {detectedChanges.summary || "Changements détectés depuis la dernière migration."}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {(detectedChanges.new_resources || []).map(r => (
                <span key={r} style={{ padding: "2px 8px", borderRadius: 5, fontSize: 10, fontWeight: 600, background: "rgba(22,163,74,0.1)", color: "#15803d", border: "1px solid rgba(22,163,74,0.2)", fontFamily: "monospace" }}>
                  + {r}
                </span>
              ))}
              {(detectedChanges.modified_files || []).slice(0, 5).map(f => (
                <span key={f} style={{ padding: "2px 8px", borderRadius: 5, fontSize: 10, fontWeight: 600, background: "rgba(217,119,6,0.1)", color: "#b45309", border: "1px solid rgba(217,119,6,0.2)", fontFamily: "monospace" }}>
                  ~ {f.split("/").pop()}
                </span>
              ))}
              {(detectedChanges.added_files || []).filter(f => !detectedChanges.new_resources?.length).slice(0, 3).map(f => (
                <span key={f} style={{ padding: "2px 8px", borderRadius: 5, fontSize: 10, fontWeight: 600, background: "rgba(14,165,233,0.1)", color: "#0284c7", border: "1px solid rgba(14,165,233,0.2)", fontFamily: "monospace" }}>
                  + {f.split("/").pop()}
                </span>
              ))}
            </div>
          </div>
          {detectedChanges.previous_sha && detectedChanges.current_sha && (
            <div style={{ fontSize: 10, color: "var(--text-disabled)", flexShrink: 0, textAlign: "right" }}>
              <div style={{ fontFamily: "monospace" }}>{detectedChanges.previous_sha.slice(0, 7)}</div>
              <div style={{ color: "#6366f1" }}>→</div>
              <div style={{ fontFamily: "monospace" }}>{detectedChanges.current_sha.slice(0, 7)}</div>
            </div>
          )}
        </div>
      )}

      {/* Unified pipeline progress — visible on all tabs whenever pipeline is not idle */}
      <PipelineProgressViewer
        status={data.status}
        deployStatus={data.deployment_status || ""}
        wsConnected={wsConnected}
        wsLastEvent={wsLastEvent}
        sseLog={sseLog}
        showLogs={showLogs}
        setShowLogs={setShowLogs}
      />

      {/* Step timeline — grisé pendant la sélection manuelle */}
      {(() => {
        const isWaiting = showServiceSelection || data?.dependency_graph?.needs_service_selection === true;
        return (
          <div style={{ position: "relative" }}>
            <StepTimeline status={data.status} data={data} sseLog={sseLog} />
            {isWaiting && (
              <div style={{
                position: "absolute", inset: 0,
                background: "rgba(248,250,252,0.75)",
                backdropFilter: "blur(1px)",
                borderRadius: 8,
                display: "flex", alignItems: "center", justifyContent: "center",
                zIndex: 10,
              }}>
                <span style={{
                  fontSize: 12, fontWeight: 600, color: "#64748b",
                  background: "#fff", border: "1px solid #e2e8f0",
                  borderRadius: 20, padding: "5px 14px",
                  boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
                }}>
                  ⏸ En attente de sélection des services
                </span>
              </div>
            )}
          </div>
        );
      })()}

      {/* Tabs */}
      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="tab-content">
        {tab === "pipeline" && (
          <div>
            {/* PipelineCICD grisé pendant la sélection manuelle */}
            {(() => {
              const isWaiting = showServiceSelection || data?.dependency_graph?.needs_service_selection === true;
              return (
                <div style={{ position: "relative" }}>
                  <PipelineCICD status={data.status} data={data} sseLog={sseLog} migrationId={data.id} />
                  {isWaiting && (
                    <div style={{
                      position: "absolute", inset: 0,
                      background: "rgba(248,250,252,0.80)",
                      backdropFilter: "blur(2px)",
                      borderRadius: 8, zIndex: 10,
                      display: "flex", alignItems: "center", justifyContent: "center",
                    }}>
                      <div style={{
                        textAlign: "center",
                        background: "#fff",
                        border: "1px solid #e2e8f0",
                        borderRadius: 12, padding: "16px 24px",
                        boxShadow: "0 4px 16px rgba(0,0,0,0.08)",
                      }}>
                        <div style={{ fontSize: 22, marginBottom: 6 }}>⏸</div>
                        <div style={{ fontSize: 13, fontWeight: 700, color: "#334155" }}>Pipeline en attente</div>
                        <div style={{ fontSize: 12, color: "#64748b", marginTop: 3 }}>
                          Sélectionnez vos services pour continuer
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}
            {hasDeployment && (
              <div style={{
                marginTop: 14, padding: "10px 14px", borderRadius: 8,
                background: "rgba(99,102,241,0.05)", border: "1px solid rgba(99,102,241,0.15)",
                fontSize: 12.5, color: "var(--text-secondary)",
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <span>🚀</span>
                {t("detail.deploy.inprogress")} <strong style={{ color: "#6366f1" }}>{t("detail.tab.deploy")}</strong> {t("detail.deploy.check")}
              </div>
            )}
          </div>
        )}
        {tab === "overview" && <OverviewPanel data={data} />}
        {tab === "infrastructure" && (
          <div style={{ height: "70vh" }}>
            <GraphSwitcher
              graph={graph}
              plan={plan}
              artifacts={data.artifacts}
              sourceCloud={data.source_cloud}
              targetCloud={data.target_cloud}
            />
          </div>
        )}
        {tab === "ai-stack" && graph && (
          <AIStackSection
            sourceCloud={data.source_cloud}
            detectedAIStack={graph.ai_stack || {}}
            targetCloud={data.target_cloud}
          />
        )}
        {tab === "plan"     && plan  && (
          <>
            {/* Warnings non-bloquants */}
            <WarningsBanner warnings={plan?.warnings ?? data?.warnings ?? []} />

            {intentIssues.length > 0 && (
              <div style={{
                display: "flex", alignItems: "flex-start", gap: 10,
                padding: "12px 16px", marginBottom: 14,
                background: "#fffbeb", border: "1px solid #f59e0b", borderRadius: 8,
              }}>
                <AlertTriangle size={16} color="#f59e0b" style={{ marginTop: 2, flexShrink: 0 }} />
                <div>
                  <strong style={{ fontSize: 13, color: "#92400e" }}>
                    Problèmes d'intent détectés ({intentIssues.length})
                  </strong>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12, color: "#78350f" }}>
                    {intentIssues.map((issue, i) => <li key={i}>{issue}</li>)}
                  </ul>
                </div>
              </div>
            )}

            <CostChart plan={plan} budget={data.budget_max_mensuel} />
            <MigrationPriority plan={plan} graph={graph} />
          </>
        )}
        {tab === "report"     && report && (
          <>
            <CostChart plan={plan} budget={data.budget_max_mensuel} />
            <PreviewReport report={report} />
          </>
        )}
        {tab === "terraform" && (
          <>
            {regionNotices.length > 0 && (
              <div style={{ marginBottom: 14 }}>
                {regionNotices.map((notice, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "flex-start", gap: 10,
                    padding: "12px 16px", marginBottom: 8,
                    background: notice.level === "error" ? "#fef2f2" : "#fffbeb",
                    border: `1px solid ${notice.level === "error" ? "#ef4444" : "#f59e0b"}`,
                    borderRadius: 8,
                  }}>
                    <span style={{ fontSize: 16, flexShrink: 0 }}>
                      {notice.level === "error" ? "🚫" : "⚠️"}
                    </span>
                    <div>
                      <strong style={{
                        fontSize: 13,
                        color: notice.level === "error" ? "#991b1b" : "#92400e",
                      }}>
                        {notice.level === "error"
                          ? `${t("detail.notice.blocked")}: ${notice.resource_type}`
                          : `${t("detail.notice.region.adjusted")}: ${notice.resource_type}`}
                      </strong>
                      <p style={{ margin: "4px 0 0", fontSize: 12, color: notice.level === "error" ? "#7f1d1d" : "#78350f" }}>
                        {notice.message}
                      </p>
                      {notice.files && (
                        <p style={{ margin: "2px 0 0", fontSize: 11, color: "#6b7280" }}>
                          {t("detail.notice.files")}: {notice.files.join(", ")}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
            <TerraformFilesPanel
              artifacts={data.artifacts}
              onRetry={needsEscalation ? () => doAction(t("detail.action.retry"), () => retryValidation(id)) : null}
            />
            {(() => {
              const corrCounts = data.correction_counts || data.artifacts?.correction_counts;
              if (!corrCounts || Object.keys(corrCounts).length === 0) return null;
              return (
                <div style={{ marginTop: 20 }}>
                  <div style={{
                    fontSize: 12, fontWeight: 700, color: "var(--text-tertiary)",
                    marginBottom: 10, textTransform: "uppercase", letterSpacing: "0.06em",
                    display: "flex", alignItems: "center", gap: 6,
                  }}>
                    <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#f59e0b", display: "inline-block" }} />
                    Cycles de correction — IaC Generator (auto-fix)
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {Object.entries(corrCounts).map(([file, count]) => {
                      const attempts = parseInt(count, 10);
                      const color = attempts >= 3 ? "#ef4444" : attempts === 2 ? "#f59e0b" : "#3b82f6";
                      const bg    = attempts >= 3 ? "#fef2f2" : attempts === 2 ? "#fffbeb" : "#eff6ff";
                      const border= attempts >= 3 ? "#fca5a5" : attempts === 2 ? "#fde68a" : "#bfdbfe";
                      const icon  = attempts >= 3 ? "❌" : attempts === 2 ? "⚠️" : "🔄";
                      return (
                        <div key={file} style={{
                          display: "flex", alignItems: "center", gap: 6,
                          padding: "6px 12px", borderRadius: 8,
                          background: bg, border: `1px solid ${border}`,
                          fontSize: 12, fontWeight: 600, color,
                        }}>
                          <span>{icon}</span>
                          <span style={{ fontFamily: "monospace" }}>{file}</span>
                          <span style={{
                            marginLeft: 4, padding: "1px 6px", borderRadius: 10,
                            background: color, color: "#fff", fontSize: 11,
                          }}>
                            {attempts}/3 tentative{attempts > 1 ? "s" : ""}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                  {Object.values(corrCounts).some(c => parseInt(c) >= 3) && (
                    <div style={{
                      marginTop: 10, padding: "10px 14px", borderRadius: 8,
                      background: "#fef2f2", border: "1px solid #fca5a5",
                      fontSize: 12, color: "#991b1b",
                    }}>
                      ⚠️ Un ou plusieurs fichiers ont atteint le maximum de 3 tentatives de correction — une escalation humaine peut avoir été déclenchée.
                    </div>
                  )}
                </div>
              );
            })()}
          </>
        )}
        {tab === "python-migrated" && (
          <div>
            <div style={{
              fontSize: 12, fontWeight: 700, color: "var(--text-tertiary)",
              marginBottom: 16, textTransform: "uppercase", letterSpacing: "0.06em",
            }}>
              Fichiers Python migrés par l'IaC Generator ({patchedPyFiles.length})
            </div>
            {/* SDK import substitutions (global, apply to all .py files) */}
            {Object.keys(sdkChanges).length > 0 && (
              <div style={{
                marginBottom: 16, padding: "12px 16px", borderRadius: 8,
                background: "linear-gradient(135deg,#eff6ff,#dbeafe)",
                border: "1px solid #bfdbfe",
              }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "#1e40af", marginBottom: 8 }}>
                  Substitutions SDK appliquées
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {Object.entries(sdkChanges).map(([src, tgt], i) => (
                    <div key={i} style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                      <code style={{ padding: "1px 6px", borderRadius: 4, background: "#fef2f2", color: "#991b1b", fontSize: 11 }}>{src}</code>
                      <span style={{ color: "#6b7280" }}>→</span>
                      <code style={{ padding: "1px 6px", borderRadius: 4, background: "#f0fdf4", color: "#15803d", fontSize: 11 }}>{tgt}</code>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {patchedPyFiles.length === 0 ? (
              <div style={{ color: "var(--text-tertiary)", fontSize: 13 }}>
                Aucun fichier Python migré.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {patchedPyFiles.map((filePath, i) => {
                  const fileName = filePath.split(/[\\/]/).pop();
                  const isRequirements = fileName === "requirements.txt";
                  const isEnv = fileName === ".env" || fileName.startsWith(".env.");
                  const isPy = fileName.endsWith(".py");
                  const iconColor = isRequirements ? "#8b5cf6" : isEnv ? "#f59e0b" : "#3b82f6";
                  const icon = isRequirements ? "📦" : isEnv ? "🔐" : "🐍";
                  return (
                    <div key={i} style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "10px 14px", borderRadius: 8,
                      background: "var(--surface-secondary, #f9fafb)",
                      border: "1px solid var(--border-color, #e5e7eb)",
                    }}>
                      <span style={{ fontSize: 16 }}>{icon}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, fontWeight: 600, fontFamily: "monospace", color: iconColor }}>
                          {fileName}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2, wordBreak: "break-all" }}>
                          {filePath}
                        </div>
                      </div>
                      <div style={{
                        flexShrink: 0, fontSize: 10, padding: "2px 8px", borderRadius: 10,
                        background: isPy ? "#eff6ff" : isRequirements ? "#f5f3ff" : "#fffbeb",
                        border: `1px solid ${isPy ? "#bfdbfe" : isRequirements ? "#ddd6fe" : "#fde68a"}`,
                        color: iconColor, fontWeight: 600,
                      }}>
                        {isPy ? "SDK migré" : isRequirements ? "packages" : "env vars"}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
        {tab === "security" && (
          <SecurityPanel artifacts={data.artifacts} />
        )}
        {tab === "deploy-cicd" && (
          <div>
            <CICDExecutionPanel
              migrationId={data.id}
              status={data.status}
              data={data}
            />
            {hasDeployment && (
              <div style={{ marginTop: 24 }}>
                <div style={{
                  fontSize: 12, fontWeight: 700, color: "var(--text-tertiary)",
                  marginBottom: 12, textTransform: "uppercase", letterSpacing: "0.06em",
                  display: "flex", alignItems: "center", gap: 6,
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e", display: "inline-block" }} />
                  Exécution Terraform en cours
                </div>
                <DeploymentPanel migrationId={data.id} prUrl={githubPrUrl} repoUrl={githubRepoUrl} />
              </div>
            )}
            {data.artifacts?.runner_job_id && (
              <div style={{ marginTop: 20 }}>
                <div style={{
                  fontSize: 12, fontWeight: 700, color: "var(--text-tertiary)",
                  marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.06em",
                }}>
                  Logs Terraform en temps réel
                </div>
                <TerraformLogsStream jobId={data.artifacts.runner_job_id} migrationId={data.id} />
              </div>
            )}
          </div>
        )}
        {tab === "cicd-delivered" && (
          <CICDPipelineDelivered data={data} migrationId={data.id} />
        )}
        {tab === "infra-monitor" && (
          <InfraHealthPanel
            migrationId={data.id}
            monthlyCostEur={plan?.summary?.estimated_total_monthly ?? plan?.summary?.total_monthly_eur ?? 0}
            deployedAt={data.updated_at}
            budgetUsd={100}
          />
        )}
        {tab === "knowledge-graph" && (
          <KnowledgeGraphPanel
            migrationId={data.id}
            migrationPlan={plan}
          />
        )}
        {tab === "health"    && healthReport && (
          <HealthPanel report={healthReport} intentIssues={intentIssues} />
        )}
      </div>

      {/* ── Floating RAG Assistant ──────────────────────────────────────── */}
      <div style={{ position: "fixed", bottom: 28, right: 28, zIndex: 1200 }}>
        {chatOpen && (
          <div style={{
            position: "absolute", bottom: 64, right: 0,
            width: 400, height: 560,
            background: "var(--surface-0,#fff)",
            borderRadius: 16,
            border: "1px solid var(--surface-3,#e2e8f0)",
            boxShadow: "0 12px 48px rgba(0,0,0,0.14)",
            overflow: "hidden",
            display: "flex", flexDirection: "column",
            animation: "slideUpFade 0.18s ease",
          }}>
            <RAGChatbot migrationId={data.id} migrationPlan={plan} />
          </div>
        )}
        <button
          onClick={() => setChatOpen((v) => !v)}
          title={chatOpen ? "Fermer l'assistant" : "Assistant RAG Migration"}
          style={{
            width: 52, height: 52, borderRadius: "50%",
            background: chatOpen
              ? "var(--surface-2,#e8edf4)"
              : "linear-gradient(135deg,#a78bfa,#7c3aed)",
            border: chatOpen ? "1px solid var(--surface-3,#e2e8f0)" : "none",
            cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: chatOpen
              ? "0 2px 8px rgba(0,0,0,0.08)"
              : "0 4px 20px rgba(124,58,237,0.45)",
            transition: "all 200ms ease",
            color: chatOpen ? "var(--text-secondary,#6b7280)" : "#fff",
          }}
        >
          {chatOpen
            ? <XIcon size={20} />
            : <Sparkles size={22} />
          }
        </button>
        {!chatOpen && (plan || data.status !== "Created") && (
          <span style={{
            position: "absolute", top: -8, right: -4,
            width: 14, height: 14, borderRadius: "50%",
            background: "#22c55e",
            border: "2px solid var(--surface-0,#fff)",
            animation: "pulse-dot 2s ease-in-out infinite",
          }} />
        )}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   PIPELINE PROGRESS VIEWER
   ══════════════════════════════════════════════════════════════════════════ */

const PIPELINE_STAGES = [
  { id: "analyze",    emoji: "🔍", labelKey: "detail.stage.analyze" },
  { id: "graph",      emoji: "🧠", labelKey: "detail.stage.graph" },
  { id: "plan",       emoji: "⚙️",  labelKey: "detail.stage.plan" },
  { id: "iac",        emoji: "🏗️", labelKey: "detail.stage.iac" },
  { id: "validate",   emoji: "🔒", labelKey: "detail.stage.validate" },
  { id: "deploy",     emoji: "🚀", labelKey: "detail.stage.deploy" },
  { id: "infra",      emoji: "☁️",  labelKey: "detail.stage.infra" },
];

// Stage indices: 0=analyze, 1=graph, 2=plan, 3=iac, 4=validate, 5=deploy, 6=infra
const PHASE_TO_STAGE = {
  iac_parser:   0,
  cooldown:     1,
  agent_01:     2,
  check_plan:   2,
  ask_human:    2,
  correct_plan: 2,
  agent_02:     3,
  agent_02_fix: 4,
  agent_03:     5,
};

const DONE_UP_TO = {
  Plan_Ready:        2,
  Reviewing:         2,
  Accepted:          2,
  Generating_IaC:    2,
  Validating_Intent: 3,
  IaC_Ready:         4,
  Deploying:         5,
  Health_Checking:   5,
  Completed:         7,
  Exported:          7,
  Correcting:        2,
};

const RUNNING_STAGE = {
  Analyzing:         0,
  Generating_IaC:    3,
  Validating_Intent: 4,
  Deploying:         5,
  Health_Checking:   6,
  Correcting:        2,
};

// Stage index of "Infra Cloud" (idx 6) — deploy-dependent stages start at "deploy" (idx 5)
const DEPLOY_STAGE_IDX = 5; // "deploy" stage
const INFRA_STAGE_IDX  = 6; // "infra" stage

function stageStateFor(idx, status, currentPhase, deployStatus) {
  const deployFailed = deployStatus === "failed" || deployStatus === "blocked";

  if (["Completed", "Exported"].includes(status)) {
    // When terraform apply failed, "deploy" stage = failed, "infra" = idle
    if (deployFailed) {
      if (idx < DEPLOY_STAGE_IDX) return "done";
      if (idx === DEPLOY_STAGE_IDX) return "failed";
      return "idle";
    }
    return "done";
  }
  if (!status || status === "Created") return "idle";

  const doneUpTo = DONE_UP_TO[status] ?? -1;
  if (idx < doneUpTo) return "done";

  if (status === "Analyzing") {
    const runningIdx = PHASE_TO_STAGE[currentPhase] ?? 0;
    if (idx < runningIdx) return "done";
    if (idx === runningIdx) return "running";
    return "idle";
  }

  if (status === "Failed" || status === "Analysis_Failed") {
    if (idx === doneUpTo) return "failed";
    return "idle";
  }

  const runningIdx = RUNNING_STAGE[status] ?? -1;
  if (idx === runningIdx) return "running";
  return "idle";
}

function PipelineProgressViewer({ status, deployStatus, wsConnected, wsLastEvent, sseLog, showLogs, setShowLogs }) {
  const { t: tl } = useI18n();
  if (!status || status === "Created") return null;

  const currentPhase = wsLastEvent?.phase ?? null;
  const isActive = ["Analyzing", "Generating_IaC", "Validating_Intent",
                    "Deploying", "Health_Checking", "Correcting",
                    "IaC_Ready", "Accepted"].includes(status);
  const deployFailed = deployStatus === "failed" || deployStatus === "blocked";
  const isFailed = status === "Failed" || status === "Analysis_Failed" || (status === "Completed" && deployFailed);
  const isDone   = (status === "Completed" || status === "Exported") && !deployFailed;

  const doneCount = PIPELINE_STAGES.filter((_, i) =>
    ["done"].includes(stageStateFor(i, status, currentPhase, deployStatus))
  ).length;
  const progress = Math.round((doneCount / PIPELINE_STAGES.length) * 100);

  const barColor = isFailed ? "#ef4444" : isDone ? "#22c55e" : "#3b82f6";

  const STATE_STYLE = {
    done:    { bg: "#f0fdf4", border: "#86efac", text: "#16a34a" },
    running: { bg: "#eff6ff", border: "#93c5fd", text: "#1d4ed8" },
    failed:  { bg: "#fef2f2", border: "#fca5a5", text: "#dc2626" },
    idle:    { bg: "var(--surface-2,#f4f6fa)", border: "var(--surface-4,#e4e8f0)", text: "var(--text-tertiary,#71717a)" },
  };

  return (
    <div style={{
      margin: "10px 0 14px",
      padding: "13px 16px",
      background: "var(--surface-1,#fafafa)",
      border: "1px solid var(--surface-3,#eef1f7)",
      borderRadius: 10,
    }}>
      {/* ── Header row ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10, fontSize: 12 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary,#0a0a0b)" }}>
          Pipeline
        </span>

        {isActive && wsConnected && (
          <span style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11,
            color: "#22c55e", fontWeight: 700 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e",
              animation: "pulse-dot 1.5s ease-in-out infinite", flexShrink: 0 }} />
            Live
          </span>
        )}

        {wsLastEvent?.phase_label && (
          <span style={{ fontSize: 11, color: "var(--text-tertiary,#71717a)" }}>
            — {wsLastEvent.phase_label}
          </span>
        )}

        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 11, color: "var(--text-tertiary,#71717a)" }}>
            {doneCount}/{PIPELINE_STAGES.length}
          </span>
          {sseLog.length > 0 && (
            <button
              onClick={() => setShowLogs((v) => !v)}
              style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "2px 9px", fontSize: 11, fontWeight: 600,
                background: showLogs ? "#1e293b" : "var(--surface-2,#f4f6fa)",
                color: showLogs ? "#e2e8f0" : "var(--text-secondary,#3f3f46)",
                border: "1px solid var(--surface-4,#e4e8f0)",
                borderRadius: 6, cursor: "pointer",
              }}
            >
              {showLogs ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
              Logs ({sseLog.length})
            </button>
          )}
        </span>
      </div>

      {/* ── Stage pills ── */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {PIPELINE_STAGES.map((stage, idx) => {
          const state = stageStateFor(idx, status, currentPhase, deployStatus);
          const s = STATE_STYLE[state] ?? STATE_STYLE.idle;
          return (
            <div
              key={stage.id}
              style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "4px 11px", borderRadius: 20,
                background: s.bg, border: `1px solid ${s.border}`,
                fontSize: 11, fontWeight: 600, color: s.text,
                transition: "background 200ms, border-color 200ms, color 200ms",
              }}
            >
              {state === "running" ? (
                <Loader2 size={11} style={{ animation: "spin 1s linear infinite", flexShrink: 0 }} />
              ) : state === "done" ? (
                <CheckCircle2 size={11} style={{ flexShrink: 0 }} />
              ) : state === "failed" ? (
                <XCircle size={11} style={{ flexShrink: 0 }} />
              ) : (
                <span style={{ fontSize: 11 }}>{stage.emoji}</span>
              )}
              {tl(stage.labelKey)}
            </div>
          );
        })}
      </div>

      {/* ── Progress bar ── */}
      <div style={{ height: 4, background: "var(--surface-3,#eef1f7)", borderRadius: 2, overflow: "hidden" }}>
        <div style={{
          height: "100%", width: `${progress}%`,
          background: barColor, borderRadius: 2,
          transition: "width 500ms cubic-bezier(0.16,1,0.3,1)",
        }} />
      </div>

      {/* ── Expandable SSE log ── */}
      {showLogs && sseLog.length > 0 && (
        <div style={{
          marginTop: 10, background: "#0f172a", borderRadius: 7,
          border: "1px solid #334155", padding: "10px 14px",
          maxHeight: 200, overflowY: "auto",
          fontFamily: "var(--font-mono,'JetBrains Mono',monospace)", fontSize: 11,
        }}>
          {sseLog.map((entry) => (
            <div key={entry.id} style={{
              padding: "2px 0",
              color: entry.error       ? "#f87171"
                   : entry.type === "phase_completed" ? "#4ade80"
                   : entry.type === "phase_started"   ? "#60a5fa"
                   : "#94a3b8",
            }}>
              <span style={{ color: "#475569", marginRight: 8 }}>{entry.ts}</span>
              <span style={{ color: "#64748b", marginRight: 6 }}>
                {entry.type === "phase_started"   ? "▶"
               : entry.type === "phase_completed" ? "✓"
               : entry.type === "error_occurred"  ? "✗" : "·"}
              </span>
              {entry.label}
              {entry.error && (
                <span style={{ color: "#f87171", marginLeft: 6 }}>
                  — {String(entry.error).slice(0, 120)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Partial Reject Panel ─────────────────────────────────────────────────── */
function PartialRejectPanel({
  resources,
  selectedServices,
  rejectionReasons,
  onToggle,
  onReason,
  onSubmit,
  onCancel,
  loading,
}) {
  const { t: tp } = useI18n();
  const selectedCount = Object.values(selectedServices).filter(Boolean).length;

  const STRATEGY_COLORS = {
    REHOST: "badge-blue",
    REPLATFORM: "badge-cyan",
    REFACTOR: "badge-purple",
    REPURCHASE: "badge-yellow",
    REARCHITECT: "badge-orange",
    RETAIN: "badge-gray",
    RETIRE: "badge-red",
  };

  return (
    <div className="partial-reject-panel" style={{
      margin: "12px 0",
      border: "1px solid var(--border-warning, #f59e0b)",
      borderRadius: 8,
      background: "var(--bg-warning-subtle, #fffbeb)",
      padding: "16px 20px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <AlertTriangle size={16} color="#f59e0b" />
        <strong style={{ fontSize: 14 }}>
          Rejet partiel — selectionnez les services a recorriger
        </strong>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--muted)" }}>
          {selectedCount} service(s) selectionne(s)
        </span>
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              <th style={{ padding: "6px 8px", textAlign: "left", width: 36 }}></th>
              <th style={{ padding: "6px 8px", textAlign: "left" }}>Service</th>
              <th style={{ padding: "6px 8px", textAlign: "left" }}>Strategie</th>
              <th style={{ padding: "6px 8px", textAlign: "left" }}>Cible</th>
              <th style={{ padding: "6px 8px", textAlign: "left", minWidth: 200 }}>
                Raison du rejet (optionnel)
              </th>
            </tr>
          </thead>
          <tbody>
            {resources.map((r, idx) => {
              const name = r.source_service || r.resource_name || r.service_name || r.name || `service-${idx}`;
              const strategy = (r.strategy || "").toUpperCase();
              const target = r.target_service || r.target_equivalent || r.target || "—";
              const checked = !!selectedServices[idx];
              return (
                <tr
                  key={idx}
                  style={{
                    borderBottom: "1px solid var(--border)",
                    background: checked ? "rgba(245,158,11,0.07)" : "transparent",
                    cursor: "pointer",
                  }}
                  onClick={() => onToggle(idx)}
                >
                  <td style={{ padding: "8px", textAlign: "center" }}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => onToggle(idx)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ cursor: "pointer", width: 15, height: 15 }}
                    />
                  </td>
                  <td style={{ padding: "8px", fontWeight: 500 }}>{name}</td>
                  <td style={{ padding: "8px" }}>
                    <span className={`badge ${STRATEGY_COLORS[strategy] || "badge-gray"}`} style={{ fontSize: 11 }}>
                      {strategy || "—"}
                    </span>
                  </td>
                  <td style={{ padding: "8px", color: "var(--muted)", fontSize: 12 }}>
                    {target}
                  </td>
                  <td style={{ padding: "8px" }} onClick={(e) => e.stopPropagation()}>
                    <input
                      className="form-input"
                      style={{ fontSize: 12, padding: "4px 8px", height: 32 }}
                      placeholder="ex: trop couteux, mauvaise region..."
                      value={rejectionReasons[idx] || ""}
                      onChange={(e) => onReason(idx, e.target.value)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 14, justifyContent: "flex-end" }}>
        <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={loading}>
          {tp("users.modal.cancel")}
        </button>
        <button
          className="btn btn-warning btn-sm"
          onClick={onSubmit}
          disabled={loading || selectedCount === 0}
        >
          {loading ? <Spinner size={14} /> : <AlertTriangle size={14} />}
          Rejeter {selectedCount > 0 ? `${selectedCount} service(s)` : "selection"}
        </button>
      </div>
    </div>
  );
}

/* ── Overview panel ──────────────────────────────────────────────────────── */
function OverviewPanel({ data }) {
  const { t: to, lang } = useI18n();
  const locale = lang === "fr" ? "fr-FR" : "en-US";
  const graph = data.dependency_graph;
  const nodes = graph?.resources?.nodes || [];
  const edges = graph?.resources?.edges || [];

  return (
    <div className="overview-grid">
      <div className="overview-card">
        <div className="overview-icon blue"><Cloud size={22} /></div>
        <div>
          <div className="overview-label">{to("create.source.label")}</div>
          <div className="overview-value">{data.source_cloud?.toUpperCase() || "—"}</div>
        </div>
      </div>
      <div className="overview-card">
        <div className="overview-icon green"><Cloud size={22} /></div>
        <div>
          <div className="overview-label">{to("create.target.label")}</div>
          <div className="overview-value">{data.target_cloud?.toUpperCase() || "—"}</div>
        </div>
      </div>
      {data.target_region && (
        <div className="overview-card">
          <div className="overview-icon cyan"><FileText size={22} /></div>
          <div>
            <div className="overview-label">{to("detail.overview.region")}</div>
            <div className="overview-value">{data.target_region}</div>
          </div>
        </div>
      )}
      <div className="overview-card">
        <div className="overview-icon purple"><GitBranch size={22} /></div>
        <div>
          <div className="overview-label">{to("detail.overview.services")}</div>
          <div className="overview-value">{nodes.length}</div>
        </div>
      </div>
      <div className="overview-card">
        <div className="overview-icon cyan"><ArrowRight size={22} /></div>
        <div>
          <div className="overview-label">{to("detail.overview.deps")}</div>
          <div className="overview-value">{edges.length}</div>
        </div>
      </div>
      <div className="overview-card">
        <div className="overview-icon yellow"><FileText size={22} /></div>
        <div>
          <div className="overview-label">{to("detail.overview.files")}</div>
          <div className="overview-value">{graph?.files_analyzed ?? "—"}</div>
        </div>
      </div>
      {data.ai_stack && Object.keys(data.ai_stack).length > 0 && (
        <div className="overview-card col-full">
          <div className="overview-icon purple"><Cpu size={22} /></div>
          <div>
            <div className="overview-label">{to("detail.overview.ai.stack")}</div>
            <div className="overview-value">
              {[
                data.ai_stack.framework,
                data.ai_stack.llm_provider,
                data.ai_stack.vector_db,
                data.ai_stack.embedding_model,
              ].filter(Boolean).join(" / ") || "—"}
            </div>
          </div>
        </div>
      )}

      {data.budget_max_mensuel != null && data.budget_max_mensuel > 0 && (
        <div className="overview-card">
          <div className="overview-icon yellow"><FileText size={22} /></div>
          <div>
            <div className="overview-label">{to("create.budget.label")}</div>
            <div className="overview-value">{data.budget_max_mensuel.toLocaleString(locale)} €</div>
          </div>
        </div>
      )}
      {data.data_residency_requirement && (
        <div className="overview-card">
          <div className="overview-icon purple"><FileText size={22} /></div>
          <div>
            <div className="overview-label">{to("detail.overview.residency")}</div>
            <div className="overview-value">{data.data_residency_requirement}</div>
          </div>
        </div>
      )}
      {data.regulatory_constraints && (
        <div className="overview-card col-full">
          <div className="overview-icon orange"><AlertTriangle size={22} /></div>
          <div>
            <div className="overview-label">{to("detail.overview.regulatory")}</div>
            <div className="overview-value" style={{ fontSize: 13 }}>{data.regulatory_constraints}</div>
          </div>
        </div>
      )}
      {data.deployment_status && (
        <div className="overview-card col-full">
          <div className="overview-icon green"><Download size={22} /></div>
          <div>
            <div className="overview-label">{to("detail.overview.deploy.status")}</div>
            <div className="overview-value">{data.deployment_status}</div>
          </div>
        </div>
      )}
      {(() => {
        const infracost = data.artifacts?.iac_validation?.estimated_monthly_cost;
        if (!infracost || infracost.error || !infracost.monthly_cost) return null;
        const cost = parseFloat(infracost.monthly_cost);
        if (!cost || cost <= 0) return null;
        const currency = infracost.currency || "USD";
        const resources = infracost.resources || [];
        return (
          <div className="overview-card col-full" style={{ background: "linear-gradient(135deg,#f0fdf4,#dcfce7)", border: "1px solid #86efac" }}>
            <div className="overview-icon green" style={{ background: "#dcfce7" }}>
              <span style={{ fontSize: 20 }}>💰</span>
            </div>
            <div style={{ flex: 1 }}>
              <div className="overview-label">Coût infrastructure estimé (Infracost)</div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                <span className="overview-value" style={{ color: "#16a34a" }}>
                  {cost.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} {currency}/mois
                </span>
                {resources.length > 0 && (
                  <span style={{ fontSize: 12, color: "#15803d" }}>
                    — {resources.length} ressource{resources.length > 1 ? "s" : ""} estimée{resources.length > 1 ? "s" : ""}
                  </span>
                )}
              </div>
              {resources.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {resources.slice(0, 6).map((r, i) => (
                    <span key={i} style={{
                      fontSize: 11, padding: "2px 8px", borderRadius: 12,
                      background: "#f0fdf4", border: "1px solid #86efac", color: "#166534",
                    }}>
                      {r.name || r.resourceType || "resource"}{r.monthlyCost ? ` — ${parseFloat(r.monthlyCost).toFixed(2)} ${currency}` : ""}
                    </span>
                  ))}
                  {resources.length > 6 && (
                    <span style={{ fontSize: 11, color: "#6b7280" }}>+{resources.length - 6} autres</span>
                  )}
                </div>
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

/* ── Cost Chart ───────────────────────────────────────────────────────────── */
function CostChart({ plan, budget }) {
  const { t: to, lang } = useI18n();
  const locale = lang === "fr" ? "fr-FR" : "en-US";
  if (!plan) return null;

  const summary = plan.summary || {};
  const sourceCost = summary.estimated_source_monthly ?? null;

  // Prefer summary total; if 0, fall back to summing individual resource costs
  let targetCost = summary.estimated_total_monthly ?? summary.estimated_target_monthly ?? null;
  if ((targetCost === null || targetCost === 0) && plan.resources?.length) {
    const summed = plan.resources.reduce((acc, r) => {
      return acc + (r.monthly_cost_eur ?? r.monthly_cost_estimate ?? 0);
    }, 0);
    if (summed > 0) targetCost = Math.round(summed * 100) / 100;
  }

  if (sourceCost === null && targetCost === null && !budget) return null;

  const maxVal = Math.max(sourceCost || 0, targetCost || 0, budget || 0, 1);
  const pct    = (v) => Math.min(100, Math.round((v / maxVal) * 100));

  const Bar = ({ value, color, label, sublabel }) => (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span style={{ color: "var(--muted)" }}>{sublabel}</span>
      </div>
      <div style={{ background: "var(--bg-subtle, #f1f5f9)", borderRadius: 6, height: 16, overflow: "hidden" }}>
        <div style={{
          width: `${pct(value)}%`, height: "100%",
          background: color, borderRadius: 6,
          transition: "width 0.6s ease",
        }} />
      </div>
    </div>
  );

  const fmt = (v) => v != null ? `${Number(v).toLocaleString(locale, { maximumFractionDigits: 0 })} €/mois` : "—";
  const savings = sourceCost != null && targetCost != null ? sourceCost - targetCost : null;

  return (
    <div style={{
      marginBottom: 20, padding: "16px 20px",
      border: "1px solid var(--border)", borderRadius: 10,
      background: "var(--card-bg, #fff)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, fontWeight: 700, fontSize: 14 }}>
        💰 Comparaison des coûts
        {savings !== null && (
          <span style={{
            marginLeft: "auto", fontSize: 12, fontWeight: 700,
            padding: "3px 10px", borderRadius: 12,
            background: savings >= 0 ? "#dcfce7" : "#fef2f2",
            color: savings >= 0 ? "#16a34a" : "#dc2626",
          }}>
            {savings >= 0 ? `−${fmt(savings)} ${to("detail.cost.savings")}` : `+${fmt(Math.abs(savings))} ${to("detail.cost.overrun")}`}
          </span>
        )}
      </div>

      {sourceCost != null && (
        <Bar value={sourceCost} color="#94a3b8" label={to("detail.cost.source")} sublabel={fmt(sourceCost)} />
      )}
      {targetCost != null && (
        <Bar value={targetCost} color="#3b82f6" label={to("detail.cost.target")} sublabel={fmt(targetCost)} />
      )}
      {budget != null && budget > 0 && (
        <Bar value={budget} color="#f59e0b" label={to("create.budget.label")} sublabel={fmt(budget)} />
      )}

      {budget != null && targetCost != null && targetCost > budget && (
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "8px 12px", background: "#fef2f2", border: "1px solid #f87171",
          borderRadius: 6, fontSize: 12, color: "#7f1d1d", marginTop: 8,
        }}>
          <AlertTriangle size={13} color="#dc2626" />
          {to("detail.cost.over.budget").replace("{n}", fmt(targetCost - budget))}
        </div>
      )}
    </div>
  );
}

/* ── Health Panel ─────────────────────────────────────────────────────────── */
function HealthPanel({ report, intentIssues }) {
  return (
    <div className="health-panel">
      {intentIssues.length > 0 && (
        <div className="health-intent-issues">
          <strong>⚠️ Problèmes d'intent détectés ({intentIssues.length})</strong>
          <ul>
            {intentIssues.map((issue, i) => (
              <li key={i}>{issue}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="health-report-content">
        <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 14, lineHeight: 1.6 }}>
          {report}
        </pre>
      </div>
    </div>
  );
}
