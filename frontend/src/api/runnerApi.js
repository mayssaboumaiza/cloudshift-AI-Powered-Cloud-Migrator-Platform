/**
 * runnerApi.js — Terraform runner job API calls.
 * Used by TerraformLogsStream and DeploymentPanel components.
 */

const BASE = "/api/v1";

async function handleResponse(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(body?.detail || `HTTP ${res.status}`);
  }
  return body;
}

/** Get job metadata (status, plan_summary, apply_outputs, etc.) */
export async function getRunnerJob(jobId) {
  const res = await fetch(`${BASE}/runner/jobs/${jobId}`);
  return handleResponse(res);
}

/** Get log lines after a cursor (paginated polling) */
export async function getJobLogs(jobId, after = 0) {
  const res = await fetch(`${BASE}/runner/jobs/${jobId}/logs?after=${after}`);
  return handleResponse(res);
}

/** Get the latest runner job for a migration */
export async function getMigrationRunnerJob(migrationId) {
  const res = await fetch(`${BASE}/migrations/${migrationId}/runner`);
  if (res.status === 404) return null;
  return handleResponse(res);
}

/** Approve a job in awaiting_approval state */
export async function approveRunnerJob(jobId) {
  const res = await fetch(`${BASE}/runner/jobs/${jobId}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return handleResponse(res);
}

/** Get GitHub Actions status for the target repo */
export async function getGithubActionsStatus(migrationId) {
  const res = await fetch(`${BASE}/migrations/${migrationId}/github-actions`);
  return handleResponse(res);
}

/**
 * Open an SSE connection to stream Terraform log lines in real time.
 * Returns an EventSource instance.
 *
 * Usage:
 *   const es = openLogStream(jobId, (line) => { ... }, () => { cleanup });
 *   // later: es.close()
 */
export function openLogStream(jobId, onLine, onTerminal) {
  const url = `${BASE}/runner/jobs/${jobId}/logs/stream`;
  const es = new EventSource(url);

  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.type === "terminal") {
        onTerminal?.(data);
        es.close();
      } else {
        onLine?.(data);
      }
    } catch {
      // skip malformed events
    }
  };

  es.onerror = () => {
    // EventSource will auto-reconnect using Last-Event-ID
  };

  return es;
}
