import { CheckCircle2, Circle, Loader2, XCircle, Clock, Timer } from "lucide-react";
import { useEffect, useState, useRef } from "react";

// ── Pipeline steps definition ─────────────────────────────────────────────────
// estSec: realistic estimate based on observed pipeline runs with Azure GPT-4o
//   - Analyzing      : GitHub API clone + AST parse + LLM stack analysis  → ~60s
//   - Generating_IaC : RAG retrieval (90K ctx) + ReAct loop + post-passes  → dynamic
//   - Adapting_Code  : LLM call per Python/env file (3-4 files average)    → ~60s
//   - Validating_Intent: LLM constraint check against plan                 → ~20s
//   - Deploying      : tf init (30s) + tf plan (30s) + tf apply (4-8 min)  → ~300s
//   - Health_Checking: HTTP probes + Azure resource status polling          → ~45s
// estSec: based on real observed pipeline runs (Azure GPT-4o, swedencentral)
// Analyzing      : GitHub clone + AST parse + LLM stack detection         → 60-90s
// Generating_IaC : RAG retrieval + ReAct loop (dynamic — set below)       → 4-10min
// Adapting_Code  : LLM call per Python/env file (3-4 files avg)           → 30-60s
// Validating_Intent: LLM constraint check                                  → 15-25s
// Deploying      : tf fmt+init (45s) + tf plan (30s) + tf apply (2-10min) → 600s avg
// Health_Checking: HTTP probes + Azure resource polling                    → 20-45s
const STEPS = [
  { key: "Created",            label: "Migration créée",                      estSec: null },
  { key: "Analyzing",          label: "Scan infrastructure et stack AI",       estSec: 30   },
  { key: "Waiting_Services",   label: "⏸ Sélection manuelle des services",    estSec: null },
  { key: "Plan_Ready",         label: "Plan de migration prêt",                estSec: null },
  { key: "Accepted",           label: "Plan accepté",                          estSec: null },
  { key: "Generating_IaC",     label: "Génération infrastructure (IaC)",       estSec: null }, // dynamic below
  { key: "Adapting_Code",      label: "Adaptation code Python (AI stack)",     estSec: 45   },
  { key: "Validating_Intent",  label: "Validation d'intent",                   estSec: 20   },
  { key: "IaC_Ready",          label: "Infrastructure validée",                estSec: null },
  { key: "Deploying",          label: "Déploiement cloud",                     estSec: 600  },
  { key: "Health_Checking",    label: "Health checks post-déploiement",        estSec: 35   },
  { key: "Completed",          label: "Migration terminée",                    estSec: null },
];

// Maps SSE phase names → step keys so we can match events to timeline steps
const PHASE_TO_STEP = {
  iac_parser:         "Analyzing",
  cooldown:           null,
  ask_user_services:  "Waiting_Services",
  agent_01:           "Plan_Ready",
  check_plan:      null,
  ask_human:       "Accepted",
  correct_plan:    "Plan_Ready",
  agent_02:        "Generating_IaC",
  validate_intent: "Validating_Intent",
  validate_iac:    "IaC_Ready",
  agent_02_fix:    "IaC_Ready",
  agent_03:        "Deploying",
  health_check:    "Health_Checking",
  export_zip:      "Completed",
  publish_github:  "Completed",
};

const STATUS_ORDER = {};
STEPS.forEach((s, i) => { STATUS_ORDER[s.key] = i; });
STATUS_ORDER["Waiting_Services"] = STATUS_ORDER["Waiting_Services"];
STATUS_ORDER["Reviewing"]       = STATUS_ORDER["Plan_Ready"];
STATUS_ORDER["Correcting"]      = STATUS_ORDER["Plan_Ready"];
STATUS_ORDER["Rejected"]        = STATUS_ORDER["Accepted"];
STATUS_ORDER["Exported"]        = STATUS_ORDER["Accepted"];
STATUS_ORDER["Analysis_Failed"] = STATUS_ORDER["Analyzing"];
STATUS_ORDER["Failed"]          = 999;

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtSec(s) {
  if (s == null || s <= 0) return null;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return sec > 0 ? `${m}min ${sec}s` : `${m}min`;
}

function fmtEst(s) {
  if (s == null) return null;
  if (s < 60) return `~${s}s`;
  return `~${Math.ceil(s / 60)}min`;
}

/** Extract actual elapsed ms between phase_started and phase_completed for a step key */
function getActualDuration(sseLog, stepKey) {
  if (!sseLog || sseLog.length === 0) return null;

  const matchingPhases = Object.entries(PHASE_TO_STEP)
    .filter(([, sk]) => sk === stepKey)
    .map(([ph]) => ph);

  if (matchingPhases.length === 0) return null;

  // Find the earliest phase_started rawTs for this step
  const started = sseLog
    .filter(e => e.type === "phase_started" && matchingPhases.includes(e.phase) && e.rawTs)
    .map(e => e.rawTs);

  // Find the latest phase_completed rawTs for this step
  const completed = sseLog
    .filter(e => e.type === "phase_completed" && matchingPhases.includes(e.phase) && e.rawTs)
    .map(e => e.rawTs);

  if (started.length === 0 || completed.length === 0) return null;

  const startMs = Math.min(...started);
  const endMs   = Math.max(...completed);
  const elapsedMs = endMs - startMs;
  return elapsedMs > 0 ? elapsedMs / 1000 : null;
}

/** Return the rawTs of phase_started for the active step (to power the live counter) */
function getActiveStartTs(sseLog, stepKey) {
  if (!sseLog || sseLog.length === 0) return null;

  const matchingPhases = Object.entries(PHASE_TO_STEP)
    .filter(([, sk]) => sk === stepKey)
    .map(([ph]) => ph);

  const entries = sseLog.filter(
    e => e.type === "phase_started" && matchingPhases.includes(e.phase) && e.rawTs
  );
  if (entries.length === 0) return null;
  return Math.min(...entries.map(e => e.rawTs));
}

// ── Live elapsed counter — stops automatically when step goes done ────────────
function LiveTimer({ startTs, frozen }) {
  const frozenAt = useRef(null);
  const [elapsed, setElapsed] = useState(startTs ? Math.floor((Date.now() - startTs) / 1000) : 0);
  const rafRef = useRef(null);

  useEffect(() => {
    if (!startTs) return;
    if (frozen) {
      // Capture the final value once and stop ticking
      if (frozenAt.current === null) {
        frozenAt.current = Math.floor((Date.now() - startTs) / 1000);
        setElapsed(frozenAt.current);
      }
      clearTimeout(rafRef.current);
      return;
    }
    frozenAt.current = null;
    const tick = () => {
      setElapsed(Math.floor((Date.now() - startTs) / 1000));
      rafRef.current = setTimeout(tick, 1000);
    };
    tick();
    return () => clearTimeout(rafRef.current);
  }, [startTs, frozen]);

  return (
    <span style={{
      marginLeft: 6, fontSize: 10, fontWeight: 700,
      padding: "1px 6px", borderRadius: 8,
      background: frozen ? "#f0fdf4" : "#eff6ff",
      color:      frozen ? "#16a34a" : "#2563eb",
      display: "inline-flex", alignItems: "center", gap: 3,
    }}>
      <Timer size={9} /> {fmtSec(elapsed) ?? "0s"}
    </span>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function StepTimeline({ status, data, sseLog = [] }) {
  // Poll the runner job — `data.status` can already say "Completed" while the
  // actual terraform apply / data-migration is still running asynchronously on
  // the runner. Without this, the "Déploiement cloud" / "Health checks" /
  // "Migration terminée" steps flip to a green check prematurely.
  const migrationId = data?.id;
  const [runnerStatus, setRunnerStatus] = useState(null);
  useEffect(() => {
    if (!migrationId) return;
    let cancelled = false;
    let id = null;
    const TERMINAL = ["done", "failed", "completed", "destroyed"];
    const poll = async () => {
      try {
        const r = await fetch(`/api/v1/migrations/${migrationId}/runner`);
        if (r.status === 404) return;
        if (r.ok) {
          const j = await r.json();
          const st = j?.status ?? null;
          if (!cancelled) setRunnerStatus(st);
          // Stop polling once the runner reaches a terminal state — no point
          // hitting /runner every 3s forever after the deployment is finished.
          if (id && TERMINAL.includes(st)) { clearInterval(id); id = null; }
        }
      } catch (_) {}
    };
    poll();
    id = setInterval(poll, 3000);
    return () => { cancelled = true; if (id) clearInterval(id); };
  }, [migrationId]);
  const runnerActive = ["queued", "init", "plan", "apply", "awaiting_approval"].includes(runnerStatus);

  const currentIdx   = STATUS_ORDER[status] ?? -1;
  const isFailed     = status === "Failed" || status === "Analysis_Failed";
  const isRejected   = status === "Rejected" || status === "Exported";
  const isCompleted  = status === "Completed";

  // Build a set of step keys confirmed DONE by real SSE phase_completed events.
  // This is the source of truth for "tick vert" — only phases that have actually
  // emitted phase_completed in the SSE stream are marked done in real time.
  const sseCompletedPhases = new Set(
    sseLog
      .filter(e => e.type === "phase_completed" && e.phase)
      .map(e => e.phase)
  );
  const sseActivePhase = sseLog
    .filter(e => e.type === "phase_started" && e.phase)
    .map(e => e.phase)
    .pop(); // last started phase = currently running

  const deployStatus = data?.deployment_status || "";
  const deployFailed = deployStatus === "failed" || deployStatus === "blocked";
  // Deployment is still running when: runner is active OR deployment_status not yet set/done.
  // The runner job may not be claimed yet when the backend first flips status="Completed",
  // so runnerActive alone misses the race window — also check deployment_status.
  const deployStillRunning = runnerActive ||
    (isCompleted && deployStatus === "running") ||
    (isCompleted && deployStatus === "" && !sseCompletedPhases.has("export_zip") &&
     !sseCompletedPhases.has("health_check"));
  const DEPLOY_START_IDX = STEPS.findIndex(s => s.key === "Deploying");

  // A step is SSE-confirmed done if ANY of its mapped phases are in sseCompletedPhases
  function isSseDone(stepKey) {
    const phases = Object.entries(PHASE_TO_STEP)
      .filter(([, sk]) => sk === stepKey)
      .map(([ph]) => ph);
    return phases.some(ph => sseCompletedPhases.has(ph));
  }

  // A step is SSE-active if its phase is the current running phase
  function isSseActive(stepKey) {
    if (!sseActivePhase) return false;
    const mappedStep = PHASE_TO_STEP[sseActivePhase];
    return mappedStep === stepKey;
  }

  const resourceCount = data?.artifacts?.expected_tf_resource_count
    ?? data?.migration_plan?.resources?.length
    ?? 0;

  // Count done steps for progress bar — prefer SSE truth when available
  const doneSteps = isCompleted
    ? (deployFailed ? DEPLOY_START_IDX : STEPS.length)
    : sseLog.length > 0
      ? STEPS.filter(s => isSseDone(s.key)).length
      : Math.max(0, currentIdx);
  const progressPct = Math.round((doneSteps / (STEPS.length - 1)) * 100);

  return (
    <div style={{ marginBottom: 20 }}>
      {/* ── compact progress bar ── */}
      {!isCompleted && !isFailed && currentIdx >= 0 && (
        <div style={{ marginBottom: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
            <span>{STEPS[Math.min(currentIdx, STEPS.length - 1)]?.label}</span>
            <span>{progressPct}%</span>
          </div>
          <div style={{ background: "var(--bg-subtle, #f1f5f9)", borderRadius: 4, height: 4, overflow: "hidden" }}>
            <div style={{
              width: `${progressPct}%`, height: "100%",
              background: isFailed ? "#ef4444" : "#3b82f6",
              borderRadius: 4, transition: "width 0.6s ease",
            }} />
          </div>
        </div>
      )}

      {/* ── step list ── */}
      <div className="step-timeline">
        {STEPS.map((step, i) => {
          let state = "pending";

          if (isCompleted && deployStillRunning && i >= DEPLOY_START_IDX) {
            // Persisted status says "Completed" but deployment is still running
            // (runner active, or deployment_status not yet set/finalized) —
            // keep deploy-dependent steps live instead of a premature green check.
            state = i === DEPLOY_START_IDX ? "active" : "pending";
          } else if (isCompleted) {
            // Pipeline finished — use deploy status for deploy-dependent steps.
            // deployFailed covers: deployment_status="failed"/"blocked" OR
            // no deployment_status at all when terraform apply was never reached.
            const deployActuallyFailed = deployFailed ||
              (deployStatus === "" && sseCompletedPhases.size > 0 &&
               !sseCompletedPhases.has("agent_03") && !sseCompletedPhases.has("health_check") &&
               !sseCompletedPhases.has("export_zip"));
            if (deployActuallyFailed && i >= DEPLOY_START_IDX) {
              state = i === DEPLOY_START_IDX ? "failed" : "pending";
            } else {
              state = "done";
            }
          } else if (sseLog.length > 0) {
            // SSE events available — use them as source of truth
            if (isSseDone(step.key)) {
              state = "done";
            } else if (isSseActive(step.key)) {
              state = "active";
            } else {
              // Fallback: steps before current status index are done if SSE has no data
              state = i < currentIdx ? "done" : "pending";
            }
          } else {
            // No SSE yet — fall back to status-order logic
            if (i < currentIdx) state = "done";
          }

          if (i === currentIdx && state === "pending") {
            if (isFailed) state = "failed";
            else if (isRejected && step.key === "Accepted") state = "rejected";
            else if (["Analyzing", "Generating_IaC", "Adapting_Code", "Validating_Intent", "Deploying", "Health_Checking"].includes(status))
              state = "active";
            else state = "current";
          }

          // ── Time badge ────────────────────────────────────────────────────
          let timeBadge = null;

          if (state === "done") {
            // Prefer exact SSE-computed duration (phase_completed - phase_started)
            const actual = getActualDuration(sseLog, step.key);
            const startTs = getActiveStartTs(sseLog, step.key);
            if (actual != null) {
              timeBadge = (
                <span style={{
                  marginLeft: 6, fontSize: 10, fontWeight: 600,
                  padding: "1px 6px", borderRadius: 8,
                  background: "#f0fdf4", color: "#16a34a",
                  display: "inline-flex", alignItems: "center", gap: 3,
                }}>
                  <Clock size={9} /> {fmtSec(actual)}
                </span>
              );
            } else if (startTs) {
              // phase_started exists but phase_completed missing (reconnect gap) —
              // show the LiveTimer frozen in green so we never display a stuck blue counter.
              timeBadge = <LiveTimer startTs={startTs} frozen />;
            }
          } else if (state === "active") {
            // Live elapsed counter if we have the start timestamp
            const startTs = getActiveStartTs(sseLog, step.key);

            // Also show estimated total time alongside the live counter
            let estSec = step.estSec;
            if (step.key === "Generating_IaC") {
              estSec = Math.max(240, resourceCount * 60);
            }

            timeBadge = (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                {startTs
                  ? <LiveTimer startTs={startTs} frozen={false} />
                  : null
                }
                {estSec != null && (
                  <span style={{
                    fontSize: 10, fontWeight: 500,
                    padding: "1px 5px", borderRadius: 8,
                    background: "#fef3c7", color: "#92400e",
                    display: "inline-flex", alignItems: "center", gap: 3,
                  }}>
                    <Clock size={9} /> estimé {fmtEst(estSec)}
                  </span>
                )}
              </span>
            );
          }

          const Icon =
            state === "done"     ? CheckCircle2 :
            state === "active"   ? Loader2      :
            state === "failed"   ? XCircle      :
            state === "rejected" ? XCircle      :
            state === "current"  ? CheckCircle2 :
            Circle;

          return (
            <div key={step.key} className={`step step-${state}`}>
              <div className="step-icon">
                <Icon size={18} className={state === "active" ? "spin" : ""} />
              </div>
              <span className="step-label">
                {step.label}
                {timeBadge}
              </span>
              {i < STEPS.length - 1 && (
                <div className={`step-line ${state === "done" ? "done" : ""}`} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
