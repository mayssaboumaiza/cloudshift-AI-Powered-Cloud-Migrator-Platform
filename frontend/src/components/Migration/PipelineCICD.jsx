/**
 * PipelineCICD.jsx — Vue pipeline CI/CD + section Exécution Terraform
 *
 * Fixes vs previous version:
 *  1. getPhaseState no longer marks ALL phases as "failed" when status="Failed".
 *     It inspects data.artifacts + data.dependency_graph + data.migration_plan
 *     to determine which phases actually completed before the failure.
 *  2. sseLog (array of SSE events from the parent) drives per-phase completion
 *     so the UI updates in real-time as each backend phase finishes.
 *  3. getStepState no longer uses a fake 50% heuristic. Sub-steps of DONE
 *     phases are all shown as done; failed phases show the specific failed step
 *     based on data.artifacts signals; active phases animate only the header.
 */
import { useState, useEffect } from "react";
import { CheckCircle2, Circle, Loader2, XCircle, AlertCircle, Terminal, GitBranch, ExternalLink, Play, Clock, RefreshCw } from "lucide-react";

// ── Terraform stage pipeline visual ──────────────────────────────────────────
const TF_STAGES = [
  { id: "fmt",       label: "terraform fmt",   desc: "Formatage du code" },
  { id: "init",      label: "terraform init",  desc: "Téléchargement providers" },
  { id: "plan",      label: "terraform plan",  desc: "Plan d'exécution" },
  { id: "apply",     label: "terraform apply", desc: "Application sur le cloud" },
  { id: "deploy_sh", label: "deploy.sh",       desc: "Migration données (S3→Blob / PostgreSQL)" },
];

function TerraformPipeline({ runnerStatus, planSummary }) {
  // deploy_sh runs after apply, before done
  const stageIndex = { init: 0, plan: 1, apply: 2, deploy_sh: 3, done: 4, failed: -1 }[runnerStatus] ?? -2;
  const fmtDone      = stageIndex >= 0;
  const initDone     = stageIndex >= 1;
  const planDone     = stageIndex >= 2;
  const applyDone    = stageIndex >= 3 || runnerStatus === "done";
  const deploySHDone = runnerStatus === "done";
  const failed       = runnerStatus === "failed";

  const stageState = (idx) => {
    if (failed) return idx === stageIndex ? "failed" : idx < stageIndex ? "done" : "pending";
    if (runnerStatus === "done") return "done";
    if (idx === 0) return fmtDone  ? "done" : runnerStatus === "init"      ? "running" : "pending";
    if (idx === 1) return initDone ? "done" : runnerStatus === "init"      ? "running" : "pending";
    if (idx === 2) return planDone ? "done" : runnerStatus === "plan"      ? "running" : "pending";
    if (idx === 3) return applyDone    ? "done" : runnerStatus === "apply"     ? "running" : "pending";
    if (idx === 4) return deploySHDone ? "done" : runnerStatus === "deploy_sh" ? "running" : "pending";
    return "pending";
  };

  const colorOf = (s) => s === "done" ? "#16a34a" : s === "running" ? "#2563eb" : s === "failed" ? "#dc2626" : "#94a3b8";
  const bgOf    = (s) => s === "done" ? "#f0fdf4" : s === "running" ? "#eff6ff" : s === "failed" ? "#fef2f2" : "var(--surface-1,#f8fafc)";
  const borderOf= (s) => s === "done" ? "#bbf7d0" : s === "running" ? "#bfdbfe" : s === "failed" ? "#fca5a5" : "var(--surface-3,#e2e8f0)";

  return (
    <div style={{ display: "flex", alignItems: "stretch", gap: 0, marginBottom: 16 }}>
      {TF_STAGES.map((st, idx) => {
        const s = stageState(idx);
        const isLast = idx === TF_STAGES.length - 1;
        return (
          <div key={st.id} style={{ display: "flex", alignItems: "center", flex: 1 }}>
            <div style={{
              flex: 1, padding: "10px 14px", borderRadius: 8,
              background: bgOf(s), border: `1px solid ${borderOf(s)}`,
              display: "flex", flexDirection: "column", gap: 3,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                {s === "running" && <Loader2 size={12} style={{ color: "#2563eb", flexShrink: 0 }} className="spin" />}
                {s === "done"    && <CheckCircle2 size={12} style={{ color: "#16a34a", flexShrink: 0 }} />}
                {s === "failed"  && <XCircle size={12} style={{ color: "#dc2626", flexShrink: 0 }} />}
                {s === "pending" && <Circle size={12} style={{ color: "#cbd5e1", flexShrink: 0 }} />}
                <span style={{ fontSize: 11, fontWeight: 700, color: colorOf(s), fontFamily: "monospace" }}>
                  {st.label}
                </span>
              </div>
              <span style={{ fontSize: 10, color: "var(--text-tertiary,#9ca3af)" }}>{st.desc}</span>
              {idx === 2 && planSummary && s === "done" && (
                <span style={{ fontSize: 10, color: "#7c3aed", fontWeight: 600 }}>
                  +{planSummary.add||0} ~{planSummary.update||0} -{planSummary.delete||0}
                </span>
              )}
            </div>
            {!isLast && (
              <div style={{
                width: 20, height: 2, flexShrink: 0,
                background: s === "done" ? "#16a34a" : "var(--surface-3,#e2e8f0)",
              }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── CI/CD Execution Panel ─────────────────────────────────────────────────────
export function CICDExecutionPanel({ migrationId, status, data }) {
  const [ghData,    setGhData]    = useState(null);
  const [runnerJob, setRunnerJob] = useState(null);
  const [logs,      setLogs]      = useState([]);
  const [lastLogId, setLastLogId] = useState(0);
  const [showLogs,  setShowLogs]  = useState(false);

  const isDeploying = ["Deploying","Health_Checking","Completed","Exported"].includes(status);
  const prUrl       = data?.artifacts?.github_pr_url  || data?.github_pr_url  || "";
  const repoUrl     = data?.artifacts?.github_repo_url || data?.github_repo_url || "";
  const tfFiles     = data?.artifacts?.terraform_files || [];
  const healthChecks = data?.artifacts?.health_checks || {};
  const deployStatus = data?.deployment_status || data?.artifacts?.deployment_status || "";
  const hasDeploy   = !!data?.deployment_manifest;

  // Detect generated scripts from artifacts
  const deployScript  = data?.artifacts?.deployment_summary?.deploy_script_path || "";
  const cicdPipeline  = data?.artifacts?.deployment_summary?.cicd_pipeline_path || data?.artifacts?.cicd_pipeline_path || "";

  // Poll GitHub Actions every 15s
  useEffect(() => {
    if (!migrationId) return;
    const poll = async () => {
      try {
        const r = await fetch(`/api/v1/migrations/${migrationId}/github-actions`);
        if (r.ok) setGhData(await r.json());
      } catch (_) {}
    };
    poll();
    const id = setInterval(poll, 15_000);
    return () => clearInterval(id);
  }, [migrationId]);

  // Poll runner job every 3s
  useEffect(() => {
    if (!migrationId) return;
    const poll = async () => {
      try {
        const r = await fetch(`/api/v1/migrations/${migrationId}/runner`);
        if (r.status === 404) return;
        if (r.ok) {
          const j = await r.json();
          setRunnerJob(j);
          if (j?.job_id && !["done","failed"].includes(j.status)) {
            const lr = await fetch(`/api/v1/runner/jobs/${j.job_id}/logs?after=${lastLogId}`);
            if (lr.ok) {
              const lines = await lr.json();
              if (lines.length > 0) {
                setLogs(prev => [...prev, ...lines].slice(-200));
                setLastLogId(lines[lines.length - 1].id);
              }
            }
          }
        }
      } catch (_) {}
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [migrationId]);

  const runnerStatus = runnerJob?.status;
  const ghRun = ghData?.latest_run;

  // ── Deployment status banner ─────────────────────────────────────────────────
  const bannerInfo = runnerStatus === "done"
    ? { bg: "linear-gradient(135deg,#f0fdf4,#dcfce7)", border: "#86efac", icon: <CheckCircle2 size={20} color="#16a34a"/>, title: "Déploiement terminé avec succès", sub: "L'infrastructure est provisionnée sur le cloud cible.", color: "#15803d" }
    : runnerStatus === "failed"
    ? { bg: "linear-gradient(135deg,#fef2f2,#fee2e2)", border: "#fca5a5", icon: <XCircle size={20} color="#dc2626"/>, title: "Échec du déploiement", sub: runnerJob?.error || "Voir les logs ci-dessous.", color: "#991b1b" }
    : runnerStatus === "apply"
    ? { bg: "linear-gradient(135deg,rgba(37,99,235,0.06),rgba(99,102,241,0.06))", border: "#bfdbfe", icon: <Loader2 size={20} color="#2563eb" className="spin"/>, title: "terraform apply en cours…", sub: "Provisionnement des ressources sur le cloud.", color: "#1d4ed8" }
    : runnerStatus === "plan"
    ? { bg: "linear-gradient(135deg,rgba(124,58,237,0.06),rgba(139,92,246,0.06))", border: "#ddd6fe", icon: <Loader2 size={20} color="#7c3aed" className="spin"/>, title: "terraform plan en cours…", sub: "Calcul du plan d'exécution (diff).", color: "#7c3aed" }
    : runnerStatus === "init"
    ? { bg: "linear-gradient(135deg,rgba(217,119,6,0.06),rgba(251,191,36,0.06))", border: "#fde68a", icon: <Loader2 size={20} color="#d97706" className="spin"/>, title: "terraform init en cours…", sub: "Téléchargement des providers Terraform.", color: "#b45309" }
    : runnerStatus === "awaiting_approval"
    ? { bg: "linear-gradient(135deg,rgba(217,119,6,0.06),rgba(251,191,36,0.06))", border: "#fde68a", icon: <Clock size={20} color="#d97706"/>, title: "En attente d'approbation du plan", sub: "Validez le terraform plan pour lancer l'apply.", color: "#b45309" }
    : deployStatus === "failed_to_enqueue"
    ? { bg: "linear-gradient(135deg,rgba(220,38,38,0.06),rgba(239,68,68,0.06))", border: "#fca5a5", icon: <XCircle size={20} color="#dc2626"/>, title: "Erreur : impossible de soumettre le job de déploiement", sub: "Le chemin des fichiers Terraform n'a pas pu être résolu. Vérifiez les logs backend.", color: "#991b1b" }
    : hasDeploy
    ? { bg: "linear-gradient(135deg,rgba(99,102,241,0.06),rgba(139,92,246,0.06))", border: "#c7d2fe", icon: <Loader2 size={20} color="#6366f1" className="spin"/>, title: "Job de déploiement soumis — en attente du worker", sub: "Le TerraformRunner va prendre en charge le job.", color: "#4f46e5" }
    : { bg: "var(--surface-1,#f8fafc)", border: "var(--surface-3,#e2e8f0)", icon: <Clock size={20} color="#94a3b8"/>, title: "En attente du lancement du déploiement", sub: "Le pipeline génère l'IaC. Le déploiement démarrera automatiquement.", color: "#64748b" };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      {/* ── Status banner ────────────────────────────────────────────────────── */}
      <div style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "16px 20px", borderRadius: 12,
        background: bannerInfo.bg, border: `1px solid ${bannerInfo.border}`,
      }}>
        {bannerInfo.icon}
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: bannerInfo.color }}>{bannerInfo.title}</div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 2 }}>{bannerInfo.sub}</div>
        </div>
        {/* GitHub Actions badge */}
        {ghData?.available && ghRun && (() => {
          const c = ghRun.conclusion === "success" ? "#16a34a" : ghRun.conclusion === "failure" ? "#dc2626" : "#2563eb";
          const lbl = ghRun.conclusion === "success" ? "✓ Success" : ghRun.conclusion === "failure" ? "✗ Failed" : "⟳ Running";
          return (
            <a href={ghRun.url || "#"} target="_blank" rel="noreferrer" style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "5px 12px", borderRadius: 7, textDecoration: "none", flexShrink: 0,
              background: c + "15", border: `1px solid ${c}30`, color: c, fontWeight: 700, fontSize: 11,
            }}>
              <GitBranch size={11} /> GitHub Actions {lbl} <ExternalLink size={9} />
            </a>
          );
        })()}
        {prUrl && (
          <a href={prUrl} target="_blank" rel="noreferrer" style={{
            padding: "6px 14px", borderRadius: 7, background: "#16a34a", color: "#fff",
            fontWeight: 700, fontSize: 11, textDecoration: "none",
            display: "flex", alignItems: "center", gap: 5, flexShrink: 0,
          }}>
            <GitBranch size={11} /> Voir la PR <ExternalLink size={10} />
          </a>
        )}
      </div>

      {/* ── Terraform pipeline stages ─────────────────────────────────────────── */}
      <div>
        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8, display: "flex", alignItems: "center", gap: 5, textTransform: "uppercase", letterSpacing: "0.05em" }}>
          <Terminal size={12} /> Exécution Terraform
        </div>
        <TerraformPipeline runnerStatus={runnerStatus} planSummary={runnerJob?.plan_summary} />
      </div>

      {/* ── Generated artifacts ───────────────────────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {/* Terraform files */}
        <div style={{ padding: "12px 14px", borderRadius: 10, background: "var(--surface-1,#f8fafc)", border: "1px solid var(--surface-3,#e2e8f0)" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8, display: "flex", alignItems: "center", gap: 5 }}>
            <Play size={11} color="#7c3aed" /> Fichiers Terraform ({tfFiles.filter(f => (typeof f === "string" ? f : f.name||"").endsWith(".tf")).length || 6})
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
            {(tfFiles.length > 0
              ? tfFiles.filter(f => {
                  const name = typeof f === "string" ? f.split("/").pop() : (f.name || "");
                  return name.endsWith(".tf");
                })
              : ["provider.tf","variables.tf","iam.tf","storage.tf","database.tf","ai.tf"]
            ).map((f, i) => {
              const name = typeof f === "string" ? f.split("/").pop() : (f.name || `file-${i}`);
              return (
                <span key={i} style={{
                  padding: "3px 8px", borderRadius: 5, fontSize: 10.5, fontWeight: 600,
                  background: "#ede9fe", color: "#5b21b6",
                  border: "1px solid #ddd6fe", fontFamily: "monospace",
                }}>{name}</span>
              );
            })}
          </div>
        </div>

        {/* Scripts générés */}
        <div style={{ padding: "12px 14px", borderRadius: 10, background: "var(--surface-1,#f8fafc)", border: "1px solid var(--surface-3,#e2e8f0)" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8, display: "flex", alignItems: "center", gap: 5 }}>
            <GitBranch size={11} color="#0891b2" /> Scripts CI/CD générés
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
            {[
              { name: "deploy.sh", ready: !!deployScript || isDeploying, desc: "Script de déploiement Azure" },
              { name: "deploy.yml", ready: !!cicdPipeline || isDeploying, desc: "GitHub Actions workflow" },
            ].map(s => (
              <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
                {s.ready
                  ? <CheckCircle2 size={12} color="#16a34a" />
                  : <Circle size={12} color="#cbd5e1" />}
                <span style={{ fontFamily: "monospace", fontWeight: 600, color: s.ready ? "#15803d" : "#94a3b8" }}>{s.name}</span>
                <span style={{ color: "var(--text-tertiary,#9ca3af)" }}>{s.desc}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Health checks ─────────────────────────────────────────────────────── */}
      {Object.keys(healthChecks).length > 0 && (
        <div style={{ padding: "12px 14px", borderRadius: 10, background: "var(--surface-1,#f8fafc)", border: "1px solid var(--surface-3,#e2e8f0)" }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8, display: "flex", alignItems: "center", gap: 5 }}>
            <CheckCircle2 size={11} color="#16a34a" /> Health checks post-déploiement
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {Object.entries(healthChecks).map(([key, val]) => {
              const ok = String(val).startsWith("✅"), warn = String(val).startsWith("⚠️");
              return (
                <div key={key} style={{ display: "flex", gap: 8, padding: "5px 10px", borderRadius: 6, fontSize: 11,
                  background: ok ? "#f0fdf4" : warn ? "#fffbeb" : "#fef2f2",
                  border: `1px solid ${ok ? "#bbf7d0" : warn ? "#fde68a" : "#fca5a5"}`,
                  color: ok ? "#166534" : warn ? "#92400e" : "#991b1b" }}>
                  <span>{ok ? "✅" : warn ? "⚠️" : "❌"}</span>
                  <span>{String(val).replace(/^[✅⚠️❌]\s?/, "")}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Terraform logs console ────────────────────────────────────────────── */}
      {logs.length > 0 && (
        <div>
          <button onClick={() => setShowLogs(v => !v)} style={{
            display: "flex", alignItems: "center", gap: 5, marginBottom: 8,
            padding: "5px 12px", borderRadius: 6, cursor: "pointer",
            background: showLogs ? "#0f172a" : "var(--surface-2)",
            color: showLogs ? "#94a3b8" : "var(--text-secondary)",
            border: "1px solid var(--surface-4)", fontSize: 11, fontWeight: 600,
          }}>
            <Terminal size={11} />
            {showLogs ? "▲ Masquer" : "▼ Afficher"} les logs Terraform ({logs.length} lignes)
          </button>
          {showLogs && (
            <div style={{
              background: "#0f172a", borderRadius: 8, padding: "12px 14px",
              maxHeight: 320, overflowY: "auto", fontSize: 11, fontFamily: "monospace", lineHeight: 1.6,
              border: "1px solid #1e293b",
            }}>
              {logs.slice(-100).map(l => (
                <div key={l.id} style={{ color: l.stream === "stderr" ? "#f87171" : l.stream === "system" ? "#94a3b8" : "#e2e8f0", padding: "1px 0" }}>
                  <span style={{ color: "#475569", marginRight: 6, fontSize: 9 }}>[{l.stage}]</span>
                  {l.line}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

    </div>
  );
}

// ── Pipeline CI/CD livré au client (deploy.yml — GitHub Actions) ─────────────
// Polls /api/v1/migrations/:id/github-actions every 10s to show the real
// status of the latest workflow run in the migrated repo.

const CICD_JOBS = [
  { id: "validate",       label: "validate",       desc: "tf fmt + tf validate + Checkov" },
  { id: "plan",           label: "plan",           desc: "terraform plan → commentaire PR" },
  { id: "apply",          label: "apply",          desc: "terraform apply — redéploiement" },
  { id: "data-migration", label: "data-migration", desc: "S3→Blob + PostgreSQL (workflow_dispatch)" },
  { id: "health-check",   label: "health-check",   desc: "TCP + HTTP probes après apply" },
  { id: "notify",         label: "notify",         desc: "Slack/email si échec" },
];

function jobStateFromRun(jobId, run) {
  if (!run) return "pending";
  // GitHub API jobs list keyed by job name
  const jobs = run.jobs || [];
  const match = jobs.find(j =>
    j.name?.toLowerCase().includes(jobId.toLowerCase()) ||
    j.name?.toLowerCase().replace(/[^a-z0-9]/g, "-") === jobId
  );
  if (!match) {
    // Derive from overall run status: if run succeeded all jobs done
    if (run.conclusion === "success") return "done";
    if (run.status === "completed" && run.conclusion !== "success") return "skipped";
    return "pending";
  }
  if (match.conclusion === "success")  return "done";
  if (match.conclusion === "failure")  return "failed";
  if (match.conclusion === "skipped")  return "skipped";
  if (match.status   === "in_progress") return "running";
  if (match.status   === "queued")      return "queued";
  return "pending";
}

function CICDJobCard({ job, state, duration }) {
  const cfg = {
    done:    { bg: "#f0fdf4", border: "#bbf7d0", color: "#15803d", icon: <CheckCircle2 size={11} color="#16a34a" /> },
    running: { bg: "#eff6ff", border: "#bfdbfe", color: "#1d4ed8", icon: <Loader2 size={11} color="#2563eb" className="spin" /> },
    failed:  { bg: "#fef2f2", border: "#fca5a5", color: "#991b1b", icon: <XCircle    size={11} color="#dc2626" /> },
    queued:  { bg: "#fffbeb", border: "#fde68a", color: "#92400e", icon: <Clock      size={11} color="#d97706" /> },
    skipped: { bg: "var(--surface-1,#f8fafc)", border: "var(--surface-3,#e2e8f0)", color: "#94a3b8", icon: <Circle size={11} color="#cbd5e1" /> },
    pending: { bg: "var(--surface-1,#f8fafc)", border: "var(--surface-3,#e2e8f0)", color: "#94a3b8", icon: <Circle size={11} color="#cbd5e1" /> },
  }[state] ?? { bg: "var(--surface-1,#f8fafc)", border: "var(--surface-3,#e2e8f0)", color: "#94a3b8", icon: <Circle size={11} color="#cbd5e1" /> };

  return (
    <div style={{
      flex: 1, padding: "10px 12px", borderRadius: 8,
      background: cfg.bg, border: `1px solid ${cfg.border}`,
      display: "flex", flexDirection: "column", gap: 3,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
        {cfg.icon}
        <span style={{ fontSize: 11, fontWeight: 700, color: cfg.color, fontFamily: "monospace" }}>
          {job.label}
        </span>
      </div>
      <span style={{ fontSize: 9, color: "var(--text-tertiary,#9ca3af)", lineHeight: 1.3 }}>{job.desc}</span>
      {duration && state === "done" && (
        <span style={{ fontSize: 9, color: cfg.color, fontWeight: 600 }}>{duration}</span>
      )}
    </div>
  );
}

export function CICDPipelineDelivered({ data, migrationId }) {
  const repoUrl    = data?.artifacts?.github_repo_url || data?.github_repo_url || "";
  const prUrl      = data?.artifacts?.github_pr_url   || data?.github_pr_url   || "";
  const cicdPath   = data?.artifacts?.cicd_pipeline_path || data?.artifacts?.deployment_summary?.cicd_pipeline_path || "";
  const [ghData,   setGhData]   = useState(null);
  const [loading,  setLoading]  = useState(true);
  const [lastPoll, setLastPoll] = useState(null);

  const id = migrationId || data?.id;

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetch(`/api/v1/migrations/${id}/github-actions`);
        if (r.ok && !cancelled) {
          setGhData(await r.json());
          setLastPoll(new Date());
        }
      } catch (_) {}
      finally { if (!cancelled) setLoading(false); }
    };
    poll();
    const timer = setInterval(poll, 10_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [id]);

  const latestRun  = ghData?.runs?.[0] || null;
  const hasRuns    = !!latestRun;
  const isRunning  = latestRun?.status === "in_progress" || latestRun?.status === "queued";
  const runConclusion = latestRun?.conclusion;

  // Overall run badge
  const runBadge = !hasRuns ? null
    : runConclusion === "success"  ? { label: "✓ Success",  color: "#16a34a", bg: "#f0fdf4",  border: "#bbf7d0" }
    : runConclusion === "failure"  ? { label: "✗ Failed",   color: "#dc2626", bg: "#fef2f2",  border: "#fca5a5" }
    : isRunning                    ? { label: "⟳ Running",  color: "#2563eb", bg: "#eff6ff",  border: "#bfdbfe" }
    :                                { label: "◎ " + (latestRun?.status || "pending"), color: "#d97706", bg: "#fffbeb", border: "#fde68a" };

  if (!repoUrl) {
    return (
      <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--text-tertiary,#9ca3af)", fontSize: 13 }}>
        Le pipeline CI/CD sera disponible ici une fois le repo migré poussé sur GitHub.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

      {/* ── Header card ── */}
      <div style={{
        padding: "14px 16px", borderRadius: 10,
        background: "var(--surface-1,#f8fafc)", border: "1px solid var(--surface-3,#e2e8f0)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <GitBranch size={13} color="#6366f1" />
          <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>
            Pipeline CI/CD livré au client
          </span>
          <code style={{ fontSize: 10, background: "rgba(99,102,241,0.08)", color: "#6366f1", padding: "2px 6px", borderRadius: 4, fontFamily: "monospace" }}>
            deploy.yml — GitHub Actions
          </code>
          {loading && <Loader2 size={11} color="#94a3b8" className="spin" />}
          {runBadge && (
            <span style={{
              marginLeft: "auto", padding: "3px 10px", borderRadius: 6, fontSize: 10, fontWeight: 700,
              color: runBadge.color, background: runBadge.bg, border: `1px solid ${runBadge.border}`,
              display: "flex", alignItems: "center", gap: 4,
            }}>
              {isRunning && <Loader2 size={9} color={runBadge.color} className="spin" />}
              {runBadge.label}
            </span>
          )}
        </div>
        {/* PR banner — shown prominently when a PR exists */}
        {prUrl && (
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 14px", borderRadius: 8, marginBottom: 8,
            background: "linear-gradient(135deg,rgba(99,102,241,0.07),rgba(139,92,246,0.07))",
            border: "1px solid rgba(99,102,241,0.25)",
          }}>
            <GitBranch size={14} color="#6366f1" style={{ flexShrink: 0 }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#4f46e5" }}>
                Pull Request ouverte — en attente de review
              </div>
              <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 1 }}>
                Les fichiers Terraform + scripts CI/CD sont sur la branche{" "}
                <code style={{ background: "rgba(0,0,0,0.05)", padding: "1px 4px", borderRadius: 3 }}>feat/cloud-migration</code>.
                Reviewez et mergez pour finaliser la migration.
              </div>
            </div>
            <a href={prUrl} target="_blank" rel="noreferrer" style={{
              flexShrink: 0, padding: "6px 14px", borderRadius: 7,
              background: "#6366f1", color: "#fff", textDecoration: "none",
              fontWeight: 700, fontSize: 11, display: "flex", alignItems: "center", gap: 5,
            }}>
              <ExternalLink size={10} /> Voir la PR
            </a>
          </div>
        )}
        <p style={{ fontSize: 10, color: "var(--text-tertiary,#9ca3af)", margin: 0, lineHeight: 1.55 }}>
          Ce pipeline a été généré par <strong>Agent 03</strong> et poussé dans le repo migré via une PR.
          Une fois mergée, il se déclenche à chaque{" "}
          <code style={{ background: "rgba(0,0,0,0.05)", padding: "1px 4px", borderRadius: 3 }}>git push</code>{" "}
          vers <code style={{ background: "rgba(0,0,0,0.05)", padding: "1px 4px", borderRadius: 3 }}>main</code>.
        </p>
        {cicdPath && (
          <div style={{ marginTop: 6, fontSize: 10, color: "#7c3aed", display: "flex", alignItems: "center", gap: 4 }}>
            <Play size={9} color="#7c3aed" />
            Fichier livré : <code style={{ fontFamily: "monospace", marginLeft: 2 }}>{cicdPath}</code>
          </div>
        )}
      </div>

      {/* ── Job pipeline ── */}
      <div style={{ display: "flex", alignItems: "stretch", gap: 0, overflowX: "auto" }}>
        {CICD_JOBS.map((job, idx) => {
          const state    = jobStateFromRun(job.id, latestRun);
          const isLast   = idx === CICD_JOBS.length - 1;
          const jobInfo  = latestRun?.jobs?.find(j => j.name?.toLowerCase().includes(job.id));
          const dur      = jobInfo?.completed_at && jobInfo?.started_at
            ? `${Math.round((new Date(jobInfo.completed_at) - new Date(jobInfo.started_at)) / 1000)}s`
            : null;
          const edgeDone = state === "done";
          return (
            <div key={job.id} style={{ display: "flex", alignItems: "center", flex: 1, minWidth: 100 }}>
              <CICDJobCard job={job} state={state} duration={dur} />
              {!isLast && (
                <div style={{
                  width: 18, height: 2, flexShrink: 0,
                  background: edgeDone ? "#16a34a" : "var(--surface-3,#e2e8f0)",
                  transition: "background 0.4s",
                }} />
              )}
            </div>
          );
        })}
      </div>

      {/* ── Run info + links ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 10, color: "var(--text-tertiary)" }}>
        {hasRuns ? (
          <>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: isRunning ? "#2563eb" : runConclusion === "success" ? "#22c55e" : "#f87171", display: "inline-block", flexShrink: 0 }} />
            <span>
              Run #{latestRun.run_number || "—"} &nbsp;·&nbsp;
              {latestRun.head_branch || "main"} &nbsp;·&nbsp;
              {latestRun.event === "push" ? "git push" : latestRun.event || "—"}
              {latestRun.created_at && <> &nbsp;·&nbsp; {new Date(latestRun.created_at).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}</>}
            </span>
          </>
        ) : (
          <>
            <span style={{ width: 7, height: 7, borderRadius: "50%", background: "#e2e8f0", display: "inline-block", flexShrink: 0 }} />
            <span>En attente du premier push sur <code style={{ background: "rgba(0,0,0,0.05)", padding: "1px 4px", borderRadius: 3 }}>main</code></span>
          </>
        )}
        {lastPoll && (
          <span style={{ color: "#cbd5e1", marginLeft: 4 }}>
            · mis à jour {lastPoll.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {latestRun?.html_url && (
            <a href={latestRun.html_url} target="_blank" rel="noreferrer" style={{ color: "#6366f1", textDecoration: "none", fontWeight: 600, display: "flex", alignItems: "center", gap: 3 }}>
              <ExternalLink size={9} /> Voir le run
            </a>
          )}
          {prUrl && (
            <a href={prUrl} target="_blank" rel="noreferrer" style={{ color: "#7c3aed", textDecoration: "none", fontWeight: 600, display: "flex", alignItems: "center", gap: 3 }}>
              <GitBranch size={9} /> PR #1
            </a>
          )}
          <a href={repoUrl} target="_blank" rel="noreferrer" style={{ color: "#6366f1", textDecoration: "none", fontWeight: 600, display: "flex", alignItems: "center", gap: 3 }}>
            <ExternalLink size={9} /> Repo
          </a>
        </div>
      </div>

    </div>
  );
}

// ── Phase definitions ─────────────────────────────────────────────────────────

const PHASES = [
  {
    id: "analyse",
    label: "Scan infrastructure et stack AI",
    description: "Détection des services cloud, frameworks IA, dépendances et AI stack",
    triggerStatuses: ["Analyzing"],
    doneStatuses: ["Plan_Ready", "Reviewing", "Accepted", "Generating_IaC", "Validating_Intent",
                   "IaC_Ready", "Deploying", "Health_Checking", "Completed", "Exported", "Rejected"],
    steps: [
      { label: "Connexion GitHub & clonage du dépôt" },
      { label: "Détection des fichiers Python, Jupyter & IaC (Terraform, CDK…)" },
      { label: "Analyse AST — imports, appels, hints cloud" },
      { label: "Détection du cloud provider (AWS / GCP / Azure)" },
      { label: "Détection du framework IA (LangChain, LlamaIndex…)" },
      { label: "Détection du stack IA (LLM, embeddings, vector store)" },
      { label: "Construction du graphe de dépendances" },
    ],
  },
  {
    id: "plan",
    label: "Génération du plan de migration",
    description: "Migration Planner — Calcul de la stratégie et du rapport de migration",
    triggerStatuses: ["Analyzing"],
    doneStatuses: ["Plan_Ready", "Reviewing", "Accepted", "Generating_IaC", "Validating_Intent",
                   "IaC_Ready", "Deploying", "Health_Checking", "Completed", "Exported", "Rejected"],
    steps: [
      { label: "Chargement des mappings cloud-to-cloud (YAML)" },
      { label: "Scoring composite (coût × perf × délai)" },
      { label: "Attribution des stratégies 7R (REHOST / REPLATFORM / REFACTOR…)" },
      { label: "Vérification disponibilité région & maturité service" },
      { label: "Génération du rapport de prévisualisation" },
    ],
  },
  {
    id: "review",
    label: "Revue et validation par le client",
    description: "Consultation du plan — Accepter ou Rejeter",
    triggerStatuses: ["Plan_Ready", "Reviewing"],
    doneStatuses: ["Accepted", "Generating_IaC", "Validating_Intent",
                   "IaC_Ready", "Deploying", "Health_Checking", "Completed"],
    rejectedStatuses: ["Rejected", "Exported"],
    steps: [
      { label: "Présentation du rapport de migration" },
      { label: "Visualisation du graphe de dépendances" },
      { label: "Décision client : Accepter ou Rejeter" },
    ],
  },
  {
    id: "iac",
    label: "Génération de l'infrastructure (IaC)",
    description: "IaC Generator — GraphRAG + Terraform + migration Python complète",
    triggerStatuses: ["Generating_IaC"],
    doneStatuses: ["Validating_Intent", "IaC_Ready", "Deploying", "Health_Checking", "Completed"],
    steps: [
      { label: "GraphRAG — récupération contexte + companion resources (PostgreSQL + pgvector)" },
      { label: "Génération provider.tf (bloc terraform + provider séparés)" },
      { label: "Génération modules Terraform par catégorie (storage.tf, database.tf…)" },
      { label: "Migration Python complète via LLM (imports + appels SDK)" },
      { label: "Validation sécurité avec Checkov" },
      { label: "Estimation des coûts avec Infracost" },
      { label: "Correction automatique des erreurs (max 3 tentatives)" },
    ],
  },
  {
    id: "intent",
    label: "Validation d'intent (LLM-as-judge)",
    description: "Vérification que l'IaC respecte les contraintes initiales",
    triggerStatuses: ["Validating_Intent"],
    doneStatuses: ["IaC_Ready", "Deploying", "Health_Checking", "Completed"],
    steps: [
      { label: "Lecture des fichiers .tf générés" },
      { label: "Comparaison région déclarée vs région Terraform" },
      { label: "Vérification budget estimé vs budget max" },
      { label: "Vérification cohérence stratégies 7R vs contraintes" },
      { label: "Rapport de validation d'intent" },
    ],
  },
  {
    id: "deploy",
    label: "Déploiement vers le cloud cible",
    description: "Deploy Orchestrator — IaC + migration données + pipeline CI/CD post-déploiement",
    triggerStatuses: ["Deploying"],
    doneStatuses: ["Health_Checking", "Completed"],
    steps: [
      { label: "Validation des credentials cloud cible" },
      { label: "Génération du script deploy.sh + pipeline CI/CD (deploy.yml)" },
      { label: "terraform init → plan → apply (provisionnement des ressources)" },
      { label: "Génération script de migration données (S3 → Blob / PostgreSQL)" },
      { label: "Génération pipeline GitHub Actions (deploy.yml)" },
      { label: "Publication artefacts sur le repo cible (si configuré)" },
    ],
  },
  {
    id: "health",
    label: "Health checks post-déploiement",
    description: "Vérification de l'infrastructure déployée",
    triggerStatuses: ["Health_Checking"],
    doneStatuses: ["Completed"],
    steps: [
      { label: "Vérification génération deploy.sh" },
      { label: "Contrôle fichiers Terraform générés" },
      { label: "Vérification statut déploiement" },
      { label: "Réconciliation coût réel vs budget" },
      { label: "Rapport de santé final (6 checks déterministes)" },
    ],
  },
];

// ── SSE phase → frontend phase ID mapping ─────────────────────────────────────
// Mirrors the CheckpointEvents emitted by the backend graph nodes.

const SSE_PHASE_TO_ID = {
  iac_parser:    "analyse",
  cooldown:      "analyse",
  agent_01:      "plan",
  check_plan:    "plan",
  correct_plan:  "plan",
  ask_human:     "review",
  agent_02:        "iac",
  validate_intent: "intent",
  validate_iac:    "iac",
  agent_02_fix:    "iac",
  // agent_03 generates deploy.sh/CI-CD; enqueue_deploy/wait_runner cover the
  // actual terraform apply on the runner — all three belong to the "deploy"
  // tile so it stays active/ticks correctly across the whole apply phase
  // instead of going stale right after agent_03 completes.
  agent_03:        "deploy",
  enqueue_deploy:  "deploy",
  wait_runner:     "deploy",
  // health_check is the actual post-deploy verification node; export_zip and
  // publish_github run after it — map all three to "health" so the tile
  // reflects real backend progress instead of waiting for the very last node.
  health_check:    "health",
  publish_github:  "health",
  export_zip:      "health",
};

// Build the set of phase IDs that had at least one SSE event (= they started/ran)
function getSSEActivatedPhases(sseLog) {
  const activated = new Set();
  for (const entry of (sseLog || [])) {
    const ids = SSE_PHASE_TO_ID[entry.phase];
    if (!ids) continue;
    for (const id of (Array.isArray(ids) ? ids : [ids])) activated.add(id);
  }
  return activated;
}

// ── Determine which phase actually failed (for status="Failed") ───────────────

function getFailedPhaseId(data, ssePhasesActivated) {
  const artifacts = data?.artifacts || {};
  const errors    = (data?.errors || []).map(e => e.toLowerCase());

  // IaC fix loop exhausted or checkov never passed → iac phase is the culprit
  if (
    artifacts.needs_human_escalation === true ||
    artifacts.iac_validation_success === false ||
    errors.some(e => e.includes("iac") || e.includes("validation") || e.includes("escalat"))
  ) return "iac";

  // No migration plan → agent_01 / plan phase failed
  if (!data?.migration_plan) return "plan";

  // No dependency graph → scan phase failed
  if (!data?.dependency_graph) return "analyse";

  // SSE says health_check/export_zip/publish_github ran → failure is post-deploy
  if (ssePhasesActivated.has("health")) return "health";

  // SSE says deploy/enqueue_deploy/wait_runner ran and something failed
  if (ssePhasesActivated.has("deploy")) return "deploy";

  return "iac"; // safe fallback
}

// ── Phase completed? (data-based, for Failed status) ─────────────────────────

function isPhaseCompleted(phaseId, data) {
  switch (phaseId) {
    case "analyse":    return !!data?.dependency_graph;
    case "plan":       return !!data?.migration_plan;
    case "review":     return !!(data?.migration_plan);  // user accepted → IaC was triggered
    case "iac":        return !!(data?.artifacts?.terraform_files?.length > 0);
    case "intent":     return !!(data?.artifacts?.intent_issues !== undefined);
    default:           return false;
  }
}

// ── Core state functions ───────────────────────────────────────────────────────

function getPhaseState(phase, status, data, ssePhasesActivated, runnerStatus) {
  // The runner job (terraform init/plan/apply on the deployment target) runs
  // asynchronously and is the ONLY authoritative source for whether the actual
  // deployment finished — `data.status` can already say "Completed" while the
  // runner is still mid-`apply`. While it's active, "deploy"/"health" must not
  // be marked done regardless of the persisted status.
  const runnerActive = ["queued", "init", "plan", "apply", "awaiting_approval"].includes(runnerStatus);
  if (runnerActive && (phase.id === "deploy" || phase.id === "health")) {
    return phase.id === "deploy" ? "active" : "pending";
  }

  if (status === "Completed" || status === "Exported") {
    // Even when the pipeline reports "Completed", the runner job (terraform apply)
    // may have actually failed — don't mark deploy/health as done in that case.
    const runnerFailed = runnerStatus === "failed";
    if (runnerFailed && phase.id === "deploy") return "failed";
    if (runnerFailed && phase.id === "health") return "pending";
    return "done";
  }

  if (phase.rejectedStatuses?.includes(status) && phase.id === "review") return "rejected";

  // ── Non-failed statuses: use declarative lists ──────────────────────────────
  if (status !== "Failed" && status !== "Analysis_Failed") {
    const thisIdx = PHASES.findIndex(p => p.id === phase.id);

    // SSE evidence is real-time and trumps a stale `status` field (the DB status
    // can lag behind — e.g. it stays "Generating_IaC" through agent_02/agent_03
    // because no node persists Validating_Intent/IaC_Ready/Deploying transitions).
    // Find the furthest-along phase that has emitted at least one SSE event and
    // derive done/active from its position rather than from `status` alone.
    let furthestSseIdx = -1;
    for (let i = 0; i < PHASES.length; i++) {
      if (ssePhasesActivated.has(PHASES[i].id)) furthestSseIdx = i;
    }

    // The SSE log can be incomplete (reconnects, page reloads mid-run only
    // replay recent events), which makes `furthestSseIdx` under-report and
    // leaves earlier tiles stuck on "active". Cross-check against the
    // artifacts/data already persisted: if a later phase's output exists,
    // every earlier phase must actually be done regardless of what the SSE
    // log captured.
    let furthestDataIdx = -1;
    for (let i = 0; i < PHASES.length; i++) {
      if (isPhaseCompleted(PHASES[i].id, data)) furthestDataIdx = i;
    }

    const furthestIdx = Math.max(furthestSseIdx, furthestDataIdx);
    if (furthestIdx > -1) {
      if (thisIdx < furthestIdx) return "done";
      if (thisIdx === furthestIdx && furthestSseIdx >= furthestDataIdx) return "active";
      if (thisIdx === furthestIdx) return "done";
    }

    if (phase.doneStatuses?.includes(status)) return "done";
    if (phase.triggerStatuses?.includes(status)) return "active";

    // Phase whose triggerStatus is earlier in the pipeline → also done
    const activePhaseIdx = PHASES.findIndex(p => p.triggerStatuses?.includes(status));
    if (activePhaseIdx > -1 && thisIdx < activePhaseIdx) return "done";
    return "pending";
  }

  // ── Failed / Analysis_Failed: smart phase-level determination ───────────────
  const failedId  = getFailedPhaseId(data, ssePhasesActivated);
  const failedIdx = PHASES.findIndex(p => p.id === failedId);
  const thisIdx   = PHASES.findIndex(p => p.id === phase.id);

  // Phase before the failed one: completed?
  if (thisIdx < failedIdx) {
    // Prefer SSE evidence, fall back to data signals
    if (ssePhasesActivated.has(phase.id) || isPhaseCompleted(phase.id, data)) return "done";
    return "done"; // if it's before the failure point, it completed by definition
  }

  if (phase.id === failedId) return "failed";

  // Phases after the failure: pending (they never ran)
  return "pending";
}

// Which sub-step index failed inside the "iac" phase, based on artifacts
function getIacFailedStepIdx(data) {
  const artifacts = data?.artifacts || {};
  // Check artifacts first (set by migration_service), fall back to top-level fields
  const humanEscalation = artifacts.needs_human_escalation ?? data?.needs_human_escalation;
  const iacValidation   = artifacts.iac_validation_success  ?? data?.iac_validation_success;
  if (humanEscalation)         return 6; // correction loop exhausted
  if (iacValidation === false)  return 4; // checkov/tf validate failed
  const errors = (data?.errors || []).map(e => e.toLowerCase());
  if (errors.some(e => e.includes("escalat")))   return 6;
  if (errors.some(e => e.includes("validation"))) return 4;
  return 0; // GraphRAG (RAG tables missing or unreachable)
}

function getStepState(stepIdx, phase, phaseState, data) {
  switch (phaseState) {
    case "done":
    case "rejected":
      return "done";

    case "pending":
      return "pending";

    case "active": {
      // Show first half as done, the midpoint as running, rest pending.
      // This is a visual indicator only — backend sub-steps are not individually tracked.
      const half = Math.floor(phase.steps.length / 2);
      if (stepIdx < half)  return "done";
      if (stepIdx === half) return "running";
      return "pending";
    }

    case "failed": {
      // For the iac phase: determine which sub-step failed from artifacts
      if (phase.id === "iac") {
        const failedAt = getIacFailedStepIdx(data);
        if (stepIdx < failedAt)  return "done";
        if (stepIdx === failedAt) return "failed";
        return "pending";
      }
      // For other phases: first sub-step shows as failed (unknown granularity)
      return stepIdx === 0 ? "failed" : "pending";
    }

    default:
      return "pending";
  }
}

// ── Icon & badge helpers ───────────────────────────────────────────────────────

function StateIcon({ state, size = 16 }) {
  if (state === "done")     return <CheckCircle2 size={size} className="icon-done" />;
  if (state === "running")  return <Loader2      size={size} className="icon-running spin" />;
  if (state === "failed")   return <XCircle      size={size} className="icon-failed" />;
  if (state === "rejected") return <AlertCircle  size={size} className="icon-rejected" />;
  return                           <Circle       size={size} className="icon-pending" />;
}

const BADGE_LABEL = {
  done:     "Terminé",
  active:   "En cours…",
  failed:   "ÉCHOUÉ",
  rejected: "Rejeté",
  pending:  "En attente",
};

// ── Phase Card ────────────────────────────────────────────────────────────────

function PhaseCard({ phase, status, data, ssePhasesActivated, runnerStatus }) {
  const phaseState = getPhaseState(phase, status, data, ssePhasesActivated, runnerStatus);

  const headerClass = {
    done:     "pipeline-phase-header phase-done",
    active:   "pipeline-phase-header phase-active",
    failed:   "pipeline-phase-header phase-failed",
    rejected: "pipeline-phase-header phase-rejected",
    pending:  "pipeline-phase-header phase-pending",
  }[phaseState] ?? "pipeline-phase-header phase-pending";

  const showSubsteps = phaseState === "active" || phaseState === "done" || phaseState === "failed";

  return (
    <div className={`pipeline-phase pipeline-phase-${phaseState}`}>
      <div className={headerClass}>
        <StateIcon state={phaseState === "active" ? "running" : phaseState} size={18} />
        <div className="pipeline-phase-title">
          <span className="pipeline-phase-name">{phase.label}</span>
          <span className="pipeline-phase-desc">{phase.description}</span>
        </div>
        <span className={`pipeline-phase-badge badge-${phaseState}`}>
          {BADGE_LABEL[phaseState] ?? phaseState}
        </span>
      </div>

      {showSubsteps && (
        <div className="pipeline-substeps">
          {phase.steps.map((step, idx) => {
            const stepState = getStepState(idx, phase, phaseState, data);
            return (
              <div key={idx} className={`pipeline-substep substep-${stepState}`}>
                <StateIcon state={stepState} size={14} />
                <span className="substep-label">{step.label}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export default function PipelineCICD({ status, data, sseLog, migrationId }) {
  const ssePhasesActivated = getSSEActivatedPhases(sseLog);

  // Poll the runner job so phase tiles can reflect the live deploy/apply state
  // — `data.status` can already say "Completed" while terraform apply is still
  // running asynchronously on the runner (see getPhaseState's runnerActive guard).
  const [runnerStatus, setRunnerStatus] = useState(null);
  useEffect(() => {
    if (!migrationId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const r = await fetch(`/api/v1/migrations/${migrationId}/runner`);
        if (r.status === 404) return;
        if (r.ok) {
          const j = await r.json();
          if (!cancelled) setRunnerStatus(j?.status ?? null);
        }
      } catch (_) {}
    };
    poll();
    const id = setInterval(poll, 3000);
    return () => { cancelled = true; clearInterval(id); };
  }, [migrationId]);

  const isRunning = [
    "Analyzing", "Generating_IaC", "Validating_Intent", "Deploying", "Health_Checking",
  ].includes(status);

  return (
    <div className="pipeline-cicd">
      <div className="pipeline-header">
        <span className="pipeline-title">Pipeline de migration</span>
        {isRunning && (
          <span className="pipeline-live-badge">
            <span className="live-dot" />
            En direct
          </span>
        )}
      </div>

      <div className="pipeline-connector">
        {PHASES.map((phase, idx) => (
          <div key={phase.id} className="pipeline-phase-wrapper">
            <PhaseCard
              phase={phase}
              status={status}
              data={data}
              ssePhasesActivated={ssePhasesActivated}
              runnerStatus={runnerStatus}
            />
            {idx < PHASES.length - 1 && (
              <div
                className={`pipeline-edge ${
                  getPhaseState(phase, status, data, ssePhasesActivated, runnerStatus) === "done" ? "edge-done" : ""
                }`}
              />
            )}
          </div>
        ))}
      </div>

      {data?.errors?.length > 0 && (
        <div className="pipeline-errors">
          <XCircle size={16} />
          <div>
            <strong>Erreurs détectées :</strong>
            <ul>{data.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
          </div>
        </div>
      )}

      {data?.warnings?.length > 0 && (
        <div className="pipeline-warnings">
          <AlertCircle size={16} />
          <div>
            <strong>Avertissements :</strong>
            <ul>{data.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </div>
        </div>
      )}

    </div>
  );
}
