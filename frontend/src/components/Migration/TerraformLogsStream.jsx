/**
 * TerraformLogsStream.jsx — Real-time Terraform deployment log viewer.
 *
 * Uses SSE (EventSource) via /api/v1/runner/jobs/{jobId}/logs/stream.
 * Falls back to polling every 3 s if SSE fails (network proxy, etc.).
 *
 * Props:
 *   jobId          — RunnerJob UUID (required)
 *   migrationId    — used to fetch job if jobId not passed
 *   autoScroll     — scroll to bottom on new lines (default: true)
 *   maxLines       — max lines to keep in memory (default: 2000)
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Terminal, CheckCircle, XCircle, Loader2, RefreshCw,
  ChevronDown, Download, Copy, ThumbsUp, AlertTriangle,
} from "lucide-react";
import { openLogStream, getJobLogs, getRunnerJob, approveRunnerJob } from "../../api/runnerApi";

const STAGE_COLORS = {
  init:              { color: "#38BDF8", label: "Init" },
  plan:              { color: "#A78BFA", label: "Plan" },
  awaiting_approval: { color: "#F59E0B", label: "Awaiting Approval" },
  apply:             { color: "#34D399", label: "Apply" },
  verify:            { color: "#60A5FA", label: "Verify" },
  done:              { color: "#22C55E", label: "Done" },
  failed:            { color: "#F87171", label: "Failed" },
  system:            { color: "#94A3B8", label: "System" },
};

const STREAM_COLORS = {
  stdout: "#E2E8F0",
  stderr: "#FCA5A5",
  system: "#A78BFA",
};

function LogLine({ line }) {
  const stageColor = STAGE_COLORS[line.stage]?.color || "#94A3B8";
  const textColor  = STREAM_COLORS[line.stream] || "#E2E8F0";
  return (
    <div style={{
      display: "flex", gap: 8, padding: "1px 0",
      fontFamily: "var(--font-mono)", fontSize: 11.5, lineHeight: 1.5,
    }}>
      <span style={{
        color: stageColor, flexShrink: 0, width: 44,
        fontWeight: 600, fontSize: 10, opacity: 0.7,
        alignSelf: "flex-start", marginTop: 1,
      }}>
        {(STAGE_COLORS[line.stage]?.label || line.stage || "").slice(0, 6).toUpperCase()}
      </span>
      <span style={{ color: textColor, wordBreak: "break-all", flex: 1 }}>
        {line.line}
      </span>
    </div>
  );
}

function JobStatusBadge({ status }) {
  const terminal  = ["done", "failed"].includes(status);
  const isRunning = !terminal && status !== "pending";
  const cfg = {
    done:              { color: "#22C55E", label: "Completed",         icon: CheckCircle },
    failed:            { color: "#F87171", label: "Failed",            icon: XCircle    },
    awaiting_approval: { color: "#F59E0B", label: "Awaiting Approval", icon: AlertTriangle },
    apply:             { color: "#34D399", label: "Applying…",         icon: Loader2    },
    plan:              { color: "#A78BFA", label: "Planning…",         icon: Loader2    },
    init:              { color: "#38BDF8", label: "Initialising…",     icon: Loader2    },
    pending:           { color: "#94A3B8", label: "Pending",           icon: Loader2    },
  }[status] || { color: "#94A3B8", label: status, icon: Loader2 };

  const Icon = cfg.icon;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "3px 10px", borderRadius: 20, fontSize: 11.5, fontWeight: 600,
      background: `${cfg.color}22`, color: cfg.color, border: `1px solid ${cfg.color}44`,
    }}>
      <Icon
        size={12}
        style={isRunning ? { animation: "spin 1.2s linear infinite" } : {}}
      />
      {cfg.label}
    </span>
  );
}

function PlanSummary({ plan }) {
  if (!plan) return null;
  const { to_add = 0, to_change = 0, to_destroy = 0, estimated_cost } = plan;
  return (
    <div style={{
      display: "flex", gap: 10, flexWrap: "wrap",
      padding: "8px 12px", borderRadius: 8,
      background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.08)",
    }}>
      <span style={{ fontSize: 11.5, color: "#94A3B8" }}>Terraform Plan:</span>
      {[
        { label: `+${to_add}`,    color: "#22C55E" },
        { label: `~${to_change}`, color: "#F59E0B" },
        { label: `-${to_destroy}`,color: "#F87171" },
      ].map(({ label, color }) => (
        <span key={label} style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700, color }}>
          {label}
        </span>
      ))}
      {estimated_cost && (
        <span style={{ fontSize: 11.5, color: "#94A3B8", marginLeft: "auto" }}>
          ~${estimated_cost}/mo
        </span>
      )}
    </div>
  );
}

export default function TerraformLogsStream({
  jobId,
  migrationId,
  autoScroll = true,
  maxLines   = 2000,
}) {
  const [job,        setJob]        = useState(null);
  const [lines,      setLines]      = useState([]);
  const [connected,  setConnected]  = useState(false);
  const [error,      setError]      = useState(null);
  const [approving,  setApproving]  = useState(false);
  const [stickBot,   setStickBot]   = useState(autoScroll);

  const bottomRef  = useRef(null);
  const esRef      = useRef(null);
  const pollRef    = useRef(null);
  const cursorRef  = useRef(0);

  /* Fetch job metadata */
  const refreshJob = useCallback(async () => {
    if (!jobId) return;
    try {
      const j = await getRunnerJob(jobId);
      setJob(j);
    } catch (e) {
      setError(e.message);
    }
  }, [jobId]);

  /* Append lines with dedup + max cap */
  const appendLines = useCallback((incoming) => {
    setLines(prev => {
      const ids  = new Set(prev.map(l => l.id));
      const newL = incoming.filter(l => !ids.has(l.id));
      if (!newL.length) return prev;
      const combined = [...prev, ...newL];
      return combined.length > maxLines ? combined.slice(-maxLines) : combined;
    });
    if (incoming.length) cursorRef.current = Math.max(...incoming.map(l => l.id));
  }, [maxLines]);

  /* Open SSE stream */
  useEffect(() => {
    if (!jobId) return;
    refreshJob();

    const es = openLogStream(
      jobId,
      (line) => {
        appendLines([line]);
        if (stickBot) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
      },
      (terminal) => {
        setConnected(false);
        refreshJob();
      },
    );
    esRef.current = es;
    setConnected(true);

    // Fallback polling every 3s when SSE fails
    es.onerror = () => {
      setConnected(false);
      if (!pollRef.current) {
        pollRef.current = setInterval(async () => {
          try {
            const rows = await getJobLogs(jobId, cursorRef.current);
            if (rows.length) appendLines(rows);
            const j = await getRunnerJob(jobId);
            setJob(j);
            if (["done", "failed"].includes(j?.status)) {
              clearInterval(pollRef.current);
              pollRef.current = null;
            }
          } catch {}
        }, 3000);
      }
    };

    return () => {
      es.close();
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [jobId]);

  /* Auto-scroll */
  useEffect(() => {
    if (stickBot) bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines, stickBot]);

  /* Approve plan */
  const handleApprove = async () => {
    if (!jobId) return;
    setApproving(true);
    try {
      await approveRunnerJob(jobId);
      await refreshJob();
    } catch (e) {
      setError(e.message);
    } finally {
      setApproving(false);
    }
  };

  /* Copy logs */
  const copyLogs = () => {
    const text = lines.map(l => `[${l.stage}] ${l.line}`).join("\n");
    navigator.clipboard?.writeText(text);
  };

  /* Download logs */
  const downloadLogs = () => {
    const text = lines.map(l => `[${l.stage}][${l.stream}] ${l.line}`).join("\n");
    const blob = new Blob([text], { type: "text/plain" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href = url; a.download = `terraform-${jobId?.slice(0, 8)}.log`; a.click();
    URL.revokeObjectURL(url);
  };

  if (!jobId) {
    return (
      <div style={{
        padding: "24px", textAlign: "center",
        color: "#94A3B8", fontFamily: "var(--font-mono)", fontSize: 12,
      }}>
        No deployment job yet.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Header bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 14px", borderRadius: 9,
        background: "#1E293B", border: "1px solid #334155",
      }}>
        <Terminal size={14} style={{ color: "#64748B", flexShrink: 0 }} />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "#64748B", flex: 1 }}>
          job/{jobId?.slice(0, 12)}…
        </span>

        {job && <JobStatusBadge status={job.status} />}

        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {/* Connected indicator */}
          <span style={{
            width: 7, height: 7, borderRadius: "50%",
            background: connected ? "#22C55E" : "#94A3B8",
            boxShadow: connected ? "0 0 6px #22C55E" : "none",
            animation: connected ? "pulse 2s infinite" : "none",
          }} />
          <button
            onClick={refreshJob}
            style={{
              background: "none", border: "none", cursor: "pointer",
              color: "#64748B", display: "flex", alignItems: "center",
            }}
            title="Refresh job status"
          >
            <RefreshCw size={13} />
          </button>
          <button
            onClick={copyLogs}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#64748B", display: "flex", alignItems: "center" }}
            title="Copy logs"
          >
            <Copy size={13} />
          </button>
          <button
            onClick={downloadLogs}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#64748B", display: "flex", alignItems: "center" }}
            title="Download logs"
          >
            <Download size={13} />
          </button>
          <button
            onClick={() => setStickBot(v => !v)}
            title={stickBot ? "Disable auto-scroll" : "Enable auto-scroll"}
            style={{
              background: stickBot ? "#0EA5E922" : "none",
              border: `1px solid ${stickBot ? "#0EA5E9" : "transparent"}`,
              borderRadius: 4, cursor: "pointer",
              color: stickBot ? "#0EA5E9" : "#64748B",
              display: "flex", alignItems: "center", padding: 2,
            }}
          >
            <ChevronDown size={13} />
          </button>
        </div>
      </div>

      {/* Plan summary */}
      {job?.plan_summary && <PlanSummary plan={job.plan_summary} />}

      {/* Approve button */}
      {job?.status === "awaiting_approval" && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          style={{
            display: "flex", alignItems: "center", gap: 12,
            padding: "12px 14px", borderRadius: 9,
            background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.3)",
          }}
        >
          <AlertTriangle size={16} style={{ color: "#F59E0B", flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 13, color: "#F59E0B" }}>
              Terraform plan ready — review and approve to apply
            </div>
            <div style={{ fontSize: 11.5, color: "#94A3B8", marginTop: 2 }}>
              Resources above will be created/modified/destroyed in your cloud account.
            </div>
          </div>
          <button
            className="btn btn-success"
            onClick={handleApprove}
            disabled={approving}
            style={{ flexShrink: 0 }}
          >
            {approving
              ? <Loader2 size={13} style={{ animation: "spin 1s linear infinite" }} />
              : <ThumbsUp size={13} />
            }
            Approve &amp; Apply
          </button>
        </motion.div>
      )}

      {error && (
        <div style={{
          padding: "8px 12px", borderRadius: 8, fontSize: 12,
          background: "rgba(248,113,113,0.1)", border: "1px solid rgba(248,113,113,0.3)",
          color: "#F87171",
        }}>
          {error}
        </div>
      )}

      {/* Log console */}
      <div style={{
        background: "#0F172A", borderRadius: 9,
        border: "1px solid #1E293B",
        height: 380, overflowY: "auto",
        padding: "10px 14px",
        scrollbarWidth: "thin",
        scrollbarColor: "#334155 transparent",
      }}>
        {lines.length === 0 ? (
          <div style={{
            height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
            color: "#334155", flexDirection: "column", gap: 8,
          }}>
            <Terminal size={22} style={{ opacity: 0.4 }} />
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
              {connected ? "Waiting for log output…" : "Connecting…"}
            </span>
          </div>
        ) : (
          lines.map((line, i) => <LogLine key={line.id ?? i} line={line} />)
        )}
        <div ref={bottomRef} />
      </div>

      {/* Apply outputs */}
      {job?.apply_outputs && Object.keys(job.apply_outputs).length > 0 && (
        <div style={{
          padding: "12px 14px", borderRadius: 9,
          background: "rgba(34,197,94,0.06)", border: "1px solid rgba(34,197,94,0.2)",
        }}>
          <div style={{ fontWeight: 700, fontSize: 12, color: "#22C55E", marginBottom: 8, textTransform: "uppercase", letterSpacing: "0.07em" }}>
            Terraform Outputs
          </div>
          {Object.entries(job.apply_outputs).map(([k, v]) => (
            <div key={k} style={{ display: "flex", gap: 8, marginBottom: 4 }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "#A78BFA", flexShrink: 0 }}>{k}</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "#E2E8F0" }}>= {JSON.stringify(v?.value ?? v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
