import { useState, useEffect, useRef, useCallback } from "react";
import { CheckCircle2, XCircle, Loader2, ChevronDown, ChevronUp, Terminal, Clock, GitBranch, ExternalLink, Play, AlertTriangle, RefreshCw, Server } from "lucide-react";

// ── GitHub Actions status badge ───────────────────────────────────────────────
function GHActionsStatus({ migrationId }) {
  const [data, setData] = useState(null);

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const res = await fetch(`/api/v1/migrations/${migrationId}/github-actions`);
        if (res.ok) { const d = await res.json(); if (alive) setData(d); }
      } catch (_) {}
      if (alive) setTimeout(poll, 15000); // poll every 15s
    }
    poll();
    return () => { alive = false; };
  }, [migrationId]);

  if (!data?.available) return null;

  const run = data.latest_run;
  const conclusion = run?.conclusion;
  const status = run?.status;

  const badge = conclusion === "success"  ? { color: "#16a34a", bg: "#dcfce7", label: "success" }
              : conclusion === "failure"  ? { color: "#dc2626", bg: "#fef2f2", label: "failed" }
              : conclusion === "cancelled"? { color: "#71717a", bg: "#f4f4f5", label: "cancelled" }
              : status === "in_progress"  ? { color: "#1d4ed8", bg: "#eff6ff", label: "running" }
              : status === "queued"       ? { color: "#92400e", bg: "#fffbeb", label: "queued" }
              :                            { color: "#71717a", bg: "#f4f4f5", label: status || "unknown" };

  return (
    <div style={{
      padding: "10px 14px", marginBottom: 12,
      background: badge.bg, border: `1px solid ${badge.color}40`,
      borderRadius: 8, fontSize: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: run ? 6 : 0 }}>
        <Play size={13} color={badge.color} />
        <span style={{ fontWeight: 700, color: badge.color }}>GitHub Actions</span>
        <span style={{
          padding: "1px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600,
          background: badge.color + "20", color: badge.color,
        }}>{badge.label}</span>
        <a href={data.repo_url} target="_blank" rel="noreferrer"
           style={{ marginLeft: "auto", color: "#6b7280", textDecoration: "none", display: "flex", alignItems: "center", gap: 3, fontSize: 11 }}>
          <GitBranch size={11} /> {data.repo_name}
        </a>
      </div>
      {run && (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: "#6b7280" }}>{run.name} — branche <code style={{ fontSize: 11 }}>{run.branch}</code></span>
          <a href={run.url} target="_blank" rel="noreferrer"
             style={{ marginLeft: "auto", color: "#3b82f6", textDecoration: "none", display: "flex", alignItems: "center", gap: 3, fontSize: 11 }}>
            Voir le run <ExternalLink size={10} />
          </a>
        </div>
      )}
      {!run && <div style={{ color: "#6b7280" }}>Aucun run — déclenchez le workflow manuellement ou poussez un commit.</div>}
    </div>
  );
}

const RUNNER_STAGES = [
  { id: "init",     label: "Terraform Init",  emoji: "⚙️"  },
  { id: "plan",     label: "Terraform Plan",  emoji: "📋" },
  { id: "apply",    label: "Terraform Apply", emoji: "🚀" },
  { id: "verify",   label: "Verification",    emoji: "🔍" },
];

// Map job status → which stage is currently running
const STATUS_TO_STAGE = {
  pending:            null,
  claimed:            null,
  init:               "init",
  plan:               "plan",
  awaiting_approval:  "plan",
  pending_apply:      "plan",
  apply:              "apply",
  verify:             "verify",
  done:               null,   // all stages done
  failed:             null,
};

// Map job status → how many stages completed before it
const DONE_BEFORE = {
  init:               0,
  plan:               1,
  awaiting_approval:  1,
  pending_apply:      1,
  apply:              2,
  verify:             3,
  done:               4,
  failed:             0,
};

function stageState(stageIdx, jobStatus, failedStage) {
  if (!jobStatus || jobStatus === "pending" || jobStatus === "claimed") return "idle";
  if (jobStatus === "failed") {
    const failIdx = RUNNER_STAGES.findIndex(s => s.id === failedStage);
    if (failIdx === stageIdx) return "failed";
    if (stageIdx < failIdx) return "done";
    return "idle";
  }
  const doneCount = DONE_BEFORE[jobStatus] ?? 0;
  if (stageIdx < doneCount) return "done";
  const running = STATUS_TO_STAGE[jobStatus];
  if (running && RUNNER_STAGES[stageIdx]?.id === running) return "running";
  return "idle";
}

const STYLE = {
  done:    { bg: "#f0fdf4", border: "#86efac", text: "#16a34a" },
  running: { bg: "#eff6ff", border: "#93c5fd", text: "#1d4ed8" },
  failed:  { bg: "#fef2f2", border: "#fca5a5", text: "#dc2626" },
  idle:    { bg: "var(--surface-2,#f4f6fa)", border: "var(--surface-4,#e4e8f0)", text: "var(--text-tertiary,#71717a)" },
};

// ── Per-resource status from apply logs ───────────────────────────────────────
function parseResourcesFromLogs(logs) {
  const resources = [];
  const seen = new Set();
  for (const entry of logs) {
    if (entry.stage !== "apply") continue;
    // "azurerm_resource_group.main: Creation complete after 2s"
    const createdMatch = entry.line.match(/^(\S+\.\S+): Creation complete/);
    if (createdMatch) {
      const name = createdMatch[1];
      if (!seen.has(name)) { seen.add(name); resources.push({ name, status: "created" }); }
      continue;
    }
    // "azurerm_resource_group.main: Modifications complete after 1s"
    const modMatch = entry.line.match(/^(\S+\.\S+): Modifications complete/);
    if (modMatch) {
      const name = modMatch[1];
      if (!seen.has(name)) { seen.add(name); resources.push({ name, status: "changed" }); }
      continue;
    }
    // "Error: creating/updating ... azurerm_sql_server.main:"
    const errMatch = entry.line.match(/Error:.*?([\w]+\.[\w.]+[\w]):/);
    if (errMatch && entry.stream === "stderr") {
      const name = errMatch[1];
      if (!seen.has(name)) { seen.add(name); resources.push({ name, status: "failed" }); }
    }
  }
  return resources;
}

function ResourceStatusTable({ resources }) {
  if (!resources.length) return null;
  const STATUS_CFG = {
    created: { icon: <CheckCircle2 size={13} color="#16a34a" />, label: "Créé", color: "#15803d", bg: "#f0fdf4" },
    changed:  { icon: <RefreshCw   size={13} color="#ca8a04" />, label: "Modifié", color: "#92400e", bg: "#fefce8" },
    failed:   { icon: <XCircle     size={13} color="#dc2626" />, label: "Échec",  color: "#991b1b", bg: "#fef2f2" },
  };
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-tertiary)", marginBottom: 5, textTransform: "uppercase", letterSpacing: "0.05em" }}>
        <Server size={11} style={{ verticalAlign: "middle", marginRight: 4 }} />
        Ressources déployées ({resources.length})
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
        {resources.map((r, i) => {
          const cfg = STATUS_CFG[r.status] || STATUS_CFG.created;
          return (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "5px 10px", borderRadius: 6,
              background: cfg.bg, fontSize: 11,
            }}>
              {cfg.icon}
              <code style={{ fontWeight: 600, color: cfg.color, flex: 1 }}>{r.name}</code>
              <span style={{ fontSize: 10, fontWeight: 700, color: cfg.color }}>{cfg.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function DeploymentPanel({ migrationId, prUrl = "", repoUrl = "" }) {
  const [job, setJob]             = useState(null);
  const [logs, setLogs]           = useState([]);
  const [lastLogId, setLastLogId] = useState(0);
  const [showLogs, setShowLogs]   = useState(false);
  const [error, setError]         = useState(null);
  const logsEndRef                = useRef(null);
  const pollRef                   = useRef(null);

  const fetchJob = useCallback(async () => {
    try {
      const res = await fetch(`/api/v1/migrations/${migrationId}/runner`);
      if (res.status === 404) return;   // job not yet queued
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setJob(data);
      return data;
    } catch (e) {
      setError(e.message);
    }
  }, [migrationId]);

  const fetchLogs = useCallback(async (jobId, after) => {
    try {
      const res = await fetch(`/api/v1/runner/jobs/${jobId}/logs?after=${after}`);
      if (!res.ok) return;
      const lines = await res.json();
      if (lines.length > 0) {
        setLogs(prev => [...prev, ...lines].slice(-500));
        setLastLogId(lines[lines.length - 1].id);
      }
    } catch (_) {}
  }, []);

  // Polling loop
  useEffect(() => {
    let active = true;

    async function poll() {
      const j = await fetchJob();
      if (!active) return;

      if (j?.job_id) {
        await fetchLogs(j.job_id, lastLogId);
      }

      const terminal = j?.status === "done" || j?.status === "failed";
      if (!terminal && active) {
        pollRef.current = setTimeout(poll, 3000);
      }
    }

    poll();
    return () => {
      active = false;
      clearTimeout(pollRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [migrationId]);

  // Incremental log fetch when job is running
  useEffect(() => {
    if (!job?.job_id) return;
    const terminal = job.status === "done" || job.status === "failed";
    if (terminal) return;

    const t = setInterval(() => {
      fetchLogs(job.job_id, lastLogId);
    }, 2000);
    return () => clearInterval(t);
  }, [job?.job_id, job?.status, lastLogId, fetchLogs]);

  useEffect(() => {
    if (showLogs) logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs, showLogs]);

  if (error) return (
    <div style={{ padding: "12px 16px", background: "#fef2f2", borderRadius: 8, color: "#dc2626", fontSize: 13 }}>
      Deployment panel error: {error}
    </div>
  );

  // ── Infrastructure ready banner (no PR/repo links — moved to Pipeline CI/CD tab) ──
  const githubHeader = (prUrl || repoUrl) ? (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "10px 14px", marginBottom: 10,
      background: "#f0fdf4", border: "1px solid #86efac", borderRadius: 8, fontSize: 12,
    }}>
      <GitBranch size={14} color="#16a34a" />
      <span style={{ fontWeight: 700, color: "#15803d" }}>Infrastructure prête</span>
    </div>
  ) : null;

  // ── GitHub Actions status ──────────────────────────────────────────────────
  const ghActions = repoUrl ? <GHActionsStatus migrationId={migrationId} /> : null;

  if (!job) return (
    <div>
      {githubHeader}
      {ghActions}
      <div style={{ padding: "20px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>
        <Loader2 size={20} style={{ animation: "spin 1s linear infinite", marginBottom: 8 }} />
        <div>En attente du job de déploiement…</div>
      </div>
    </div>
  );

  const failedStage = job.status === "failed" ? (job.error_class?.toLowerCase().replace("_failed","") || null) : null;
  const isDone   = job.status === "done";
  const isFailed = job.status === "failed";
  const isWaiting = job.status === "awaiting_approval";

  const doneCount = isDone ? 4 : (DONE_BEFORE[job.status] ?? 0);
  const progress  = Math.round((doneCount / 4) * 100);

  return (
    <div>
      {githubHeader}
      {ghActions}
    <div style={{
      padding: "16px 18px",
      background: "var(--surface-1,#fafafa)",
      border: "1px solid var(--surface-3,#eef1f7)",
      borderRadius: 10,
      marginBottom: 12,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>Déploiement Terraform</span>
        <span style={{
          fontSize: 11, padding: "2px 8px", borderRadius: 10, fontWeight: 600,
          background: isDone ? "#dcfce7" : isFailed ? "#fef2f2" : "#eff6ff",
          color:      isDone ? "#16a34a" : isFailed ? "#dc2626" : "#1d4ed8",
        }}>
          {job.status}
        </span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-tertiary)" }}>
          <Clock size={11} style={{ marginRight: 4, verticalAlign: "middle" }} />
          {job.created_at ? new Date(job.created_at).toLocaleTimeString("fr-FR") : "—"}
        </span>
      </div>

      {/* Stage pills */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {RUNNER_STAGES.map((stage, idx) => {
          const state = stageState(idx, job.status, failedStage);
          const s = STYLE[state];
          return (
            <div key={stage.id} style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "4px 12px", borderRadius: 20,
              background: s.bg, border: `1px solid ${s.border}`,
              fontSize: 11, fontWeight: 600, color: s.text,
              transition: "all 200ms",
            }}>
              {state === "running" ? <Loader2 size={11} style={{ animation: "spin 1s linear infinite" }} />
               : state === "done"    ? <CheckCircle2 size={11} />
               : state === "failed"  ? <XCircle size={11} />
               : <span>{stage.emoji}</span>}
              {stage.label}
            </div>
          );
        })}
      </div>

      {/* Progress bar */}
      <div style={{ height: 4, background: "var(--surface-3,#eef1f7)", borderRadius: 2, overflow: "hidden", marginBottom: 10 }}>
        <div style={{
          height: "100%", width: `${progress}%`,
          background: isFailed ? "#ef4444" : isDone ? "#22c55e" : "#3b82f6",
          borderRadius: 2, transition: "width 600ms cubic-bezier(0.16,1,0.3,1)",
        }} />
      </div>

      {/* Approval gate */}
      {isWaiting && (
        <div style={{
          padding: "10px 14px", background: "#fffbeb", border: "1px solid #f59e0b",
          borderRadius: 7, fontSize: 12, color: "#92400e", marginBottom: 10,
        }}>
          ⏳ En attente d'approbation du plan Terraform — vérifiez le résumé du plan puis approuvez.
        </div>
      )}

      {/* Plan summary */}
      {job.plan_summary && (
        <div style={{ display: "flex", gap: 10, marginBottom: 10, flexWrap: "wrap" }}>
          {[
            { label: "Add",    value: job.plan_summary.add,    color: "#22c55e" },
            { label: "Change", value: job.plan_summary.update, color: "#f59e0b" },
            { label: "Delete", value: job.plan_summary.delete, color: "#ef4444" },
          ].map(({ label, value, color }) => (
            <div key={label} style={{
              padding: "4px 12px", borderRadius: 8, fontSize: 12, fontWeight: 600,
              background: "var(--surface-2)", color,
            }}>
              {label}: {value ?? 0}
            </div>
          ))}
        </div>
      )}

      {/* Apply outputs */}
      {isDone && job.apply_outputs && Object.keys(job.apply_outputs).length > 0 && (
        <div style={{
          padding: "10px 14px", background: "#f0fdf4", border: "1px solid #86efac",
          borderRadius: 7, fontSize: 12, marginBottom: 10,
        }}>
          <strong style={{ display: "block", marginBottom: 6, color: "#16a34a" }}>
            Outputs Terraform
          </strong>
          {Object.entries(job.apply_outputs).map(([k, v]) => (
            <div key={k} style={{ fontFamily: "monospace", color: "#374151" }}>
              <span style={{ color: "#6b7280" }}>{k}:</span> {String(v)}
            </div>
          ))}
        </div>
      )}

      {/* Per-resource apply status */}
      {(isDone || isFailed) && logs.length > 0 && (
        <ResourceStatusTable resources={parseResourcesFromLogs(logs)} />
      )}

      {/* Failure alert */}
      {isFailed && (() => {
        const errText = String(job.error || "").toLowerCase();
        const isQuota  = errText.includes("insufficientquota") || errText.includes("insufficient quota");
        const isAuthz  = errText.includes("authorizationfailed") && errText.includes("roleassignments");
        const isVnet   = errText.includes("conflictingpublicnetwork");

        const hints = [];
        if (isQuota)  hints.push({ icon: "📊", text: "Quota OpenAI épuisé dans cette région — changer la location de azurerm_cognitive_account (ex: eastus, westeurope, swedencentral)" });
        if (isAuthz)  hints.push({ icon: "🔐", text: "Service principal sans droit roleAssignments/write — le role assignment est désormais désactivé par défaut (count=0). Relancez la génération IaC." });
        if (isVnet)   hints.push({ icon: "🌐", text: "Conflit public_network_access + delegated_subnet sur PostgreSQL — relancez la génération IaC pour corriger automatiquement." });

        return (
          <div style={{
            padding: "11px 14px", background: "#fef2f2", border: "1px solid #fca5a5",
            borderRadius: 7, fontSize: 12, marginBottom: 10,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: hints.length ? 8 : 0 }}>
              <AlertTriangle size={16} color="#dc2626" style={{ flexShrink: 0 }} />
              <span style={{ fontWeight: 700, color: "#991b1b" }}>
                Déploiement échoué{job.error_class ? ` — étape ${job.error_class}` : ""}
              </span>
            </div>
            {hints.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 8 }}>
                {hints.map((h, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "flex-start", gap: 7,
                    padding: "7px 10px", background: "#fff7ed", border: "1px solid #fed7aa",
                    borderRadius: 6, color: "#92400e", fontSize: 11.5, lineHeight: 1.5,
                  }}>
                    <span style={{ flexShrink: 0 }}>{h.icon}</span>
                    <span>{h.text}</span>
                  </div>
                ))}
              </div>
            )}
            {job.error && (
              <div style={{ color: "#7f1d1d", fontFamily: "monospace", fontSize: 11, whiteSpace: "pre-wrap" }}>
                {String(job.error).slice(0, 400)}
              </div>
            )}
          </div>
        );
      })()}

      {/* Logs toggle */}
      {logs.length > 0 && (
        <>
          <button
            onClick={() => setShowLogs(v => !v)}
            style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "4px 10px", fontSize: 11, fontWeight: 600, cursor: "pointer",
              background: showLogs ? "#1e293b" : "var(--surface-2)",
              color: showLogs ? "#e2e8f0" : "var(--text-secondary)",
              border: "1px solid var(--surface-4)", borderRadius: 6,
            }}
          >
            <Terminal size={11} />
            {showLogs ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            Logs ({logs.length})
          </button>

          {showLogs && (
            <div style={{
              marginTop: 8, background: "#0f172a", borderRadius: 7,
              border: "1px solid #334155", padding: "10px 14px",
              maxHeight: 280, overflowY: "auto",
              fontFamily: "var(--font-mono,'JetBrains Mono',monospace)", fontSize: 11,
            }}>
              {logs.map(entry => (
                <div key={entry.id} style={{
                  padding: "1px 0",
                  color: entry.stream === "stderr" ? "#f87171"
                       : entry.stream === "system"  ? "#a78bfa"
                       : "#94a3b8",
                }}>
                  <span style={{ color: "#475569", marginRight: 6 }}>[{entry.stage}]</span>
                  {entry.line}
                </div>
              ))}
              <div ref={logsEndRef} />
            </div>
          )}
        </>
      )}
    </div>
    </div>
  );
}
