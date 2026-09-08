const BASE = "/api/v1/migrations";
const GITHUB_BASE = "/api/v1/github";

async function request(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (res.status === 204) return null;
  const data = await res.json();
  if (!res.ok) {
    // Handle different error formats:
    // 1. Pydantic validation errors (422): data.detail is array of objects
    // 2. Custom error: data.detail is string
    // 3. Fallback: JSON.stringify full response
    let msg = "";
    if (Array.isArray(data.detail)) {
      // Pydantic validation errors: extract field + message
      msg = data.detail
        .map((err) => `${err.loc?.[1] || err.loc?.[0]}: ${err.msg}`)
        .join("\n");
    } else if (data.detail) {
      // detail can be a plain string or a structured object (e.g. missing_credentials).
      // Always produce a string so new Error(msg) has a parseable .message.
      msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } else if (data.message) {
      msg = data.message;
    } else {
      msg = JSON.stringify(data);
    }
    throw new Error(msg);
  }
  return data;
}

/* ── CRUD ───────────────────────────────────────────────────────────────── */

export function fetchMigrations() {
  return request(BASE + "/");
}

export function fetchMigration(id) {
  return request(`${BASE}/${id}`);
}

export function createMigration(payload) {
  return request(BASE + "/", { method: "POST", body: JSON.stringify(payload) });
}

export function updateMigration(id, payload) {
  return request(`${BASE}/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteMigration(id) {
  return request(`${BASE}/${id}`, { method: "DELETE" });
}

/* ── Workflow ────────────────────────────────────────────────────────────── */

export function startAnalysis(id) {
  return request(`${BASE}/${id}/analyze`, { method: "POST" });
}

export function retryValidation(id) {
  return request(`${BASE}/${id}/retry-validation`, { method: "POST" });
}

export function acceptPlan(id) {
  return request(`${BASE}/${id}/accept`, { method: "POST" });
}

export function rejectPlan(id) {
  return request(`${BASE}/${id}/reject`, { method: "POST" });
}

export function partialRejectPlan(id, rejectedServices, rejectionReasons) {
  return request(`${BASE}/${id}/partial-reject`, {
    method: "POST",
    body: JSON.stringify({
      rejected_services: rejectedServices,
      rejection_reasons: rejectionReasons,
    }),
  });
}

export function submitServices(id, services) {
  return request(`${BASE}/${id}/submit-services`, {
    method: "POST",
    body: JSON.stringify({ services }),
  });
}

/* ── Credentials ─────────────────────────────────────────────────────────── */

export function storeCredentials(migrationId, provider, credentials) {
  return request("/api/v1/credentials/store", {
    method: "POST",
    body: JSON.stringify({ user_id: migrationId, provider, credentials }),
  });
}

/* ── Health ──────────────────────────────────────────────────────────────── */

export function healthCheck() {
  return request("/health");
}

/* ── GitHub Intake ─────────────────────────────────────────────────────── */

export function fetchGithubRepos(githubToken, githubUrl) {
  const params = new URLSearchParams({
    github_token: githubToken,
    github_url: githubUrl,
  });
  return request(`${GITHUB_BASE}/repos?${params.toString()}`);
}

export function analyzeGithubSelection(payload) {
  return request(`${GITHUB_BASE}/analyze`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function dbHealthCheck() {
  const data = await request("/health");
  return {
    api: data.status,
    version: data.version,
    db: data.database?.status,
    dbDetail: data.database?.detail,
  };
}
