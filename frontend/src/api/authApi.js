/**
 * authApi.js — Auth API calls (login, register, refresh, me, user CRUD).
 * Wired but NOT exposed in the UI yet — activate by wrapping routes with
 * <ProtectedRoute> in App.jsx when ready.
 *
 * Token storage: localStorage
 *   cs_access_token   — short-lived JWT
 *   cs_refresh_token  — long-lived JWT
 *   cs_user           — serialised user object (cache)
 */

const BASE = "/api/v1/auth";

function authHeaders(extra = {}) {
  const token = localStorage.getItem("cs_access_token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

async function handleResponse(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg;
    if (Array.isArray(body?.detail)) {
      msg = body.detail.map((e) => e.msg).join(", ");
    } else if (typeof body?.detail === "string") {
      msg = body.detail;
    } else {
      msg = `HTTP ${res.status}`;
    }
    throw new Error(msg);
  }
  return body;
}

// ── Auth ────────────────────────────────────────────────────────────────────

export async function login(email, password) {
  const res = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await handleResponse(res);
  localStorage.setItem("cs_access_token",  data.access_token);
  localStorage.setItem("cs_refresh_token", data.refresh_token);
  return data;
}

export async function register(email, username, password) {
  const res = await fetch(`${BASE}/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username, password, role: "admin" }),
  });
  await handleResponse(res);
}

// ── Invitations ──────────────────────────────────────────────────────────────

export async function inviteUser(email, role) {
  const res = await fetch(`${BASE}/invite`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ email, role }),
  });
  return handleResponse(res);
}

export async function listInvitations() {
  const res = await fetch(`${BASE}/invitations`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function revokeInvitation(id) {
  const res = await fetch(`${BASE}/invitations/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (res.status === 204) return;
  return handleResponse(res);
}

export async function getInviteInfo(token) {
  const res = await fetch(`${BASE}/invite/${token}`);
  return handleResponse(res);
}

export async function acceptInvite(token, username, password) {
  const res = await fetch(`${BASE}/invite/${token}/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const data = await handleResponse(res);
  localStorage.setItem("cs_access_token",  data.access_token);
  localStorage.setItem("cs_refresh_token", data.refresh_token);
  return data;
}

export async function refreshTokens() {
  const refresh_token = localStorage.getItem("cs_refresh_token");
  if (!refresh_token) throw new Error("No refresh token");
  const res = await fetch(`${BASE}/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  const data = await handleResponse(res);
  localStorage.setItem("cs_access_token",  data.access_token);
  localStorage.setItem("cs_refresh_token", data.refresh_token);
  return data;
}

export function logout() {
  localStorage.removeItem("cs_access_token");
  localStorage.removeItem("cs_refresh_token");
  localStorage.removeItem("cs_user");
}

export async function getMe() {
  const res = await fetch(`${BASE}/me`, { headers: authHeaders() });
  const user = await handleResponse(res);
  localStorage.setItem("cs_user", JSON.stringify(user));
  return user;
}

export async function changePassword(currentPassword, newPassword) {
  const res = await fetch(`${BASE}/me/password`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  if (res.status === 204) return;
  return handleResponse(res);
}

// ── User CRUD (admin only) ────────────────────────────────────────────────────

export async function listUsers(skip = 0, limit = 50) {
  const res = await fetch(`${BASE}/users?skip=${skip}&limit=${limit}`, {
    headers: authHeaders(),
  });
  return handleResponse(res);
}

export async function getUser(userId) {
  const res = await fetch(`${BASE}/users/${userId}`, { headers: authHeaders() });
  return handleResponse(res);
}

export async function createUser(payload) {
  const res = await fetch(`${BASE}/users`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function updateUser(userId, payload) {
  const res = await fetch(`${BASE}/users/${userId}`, {
    method: "PATCH",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  return handleResponse(res);
}

export async function deleteUser(userId) {
  const res = await fetch(`${BASE}/users/${userId}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (res.status === 204) return;
  return handleResponse(res);
}

// ── Token helpers ─────────────────────────────────────────────────────────────

export function getStoredUser() {
  try {
    const raw = localStorage.getItem("cs_user");
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function isAuthenticated() {
  return Boolean(localStorage.getItem("cs_access_token"));
}

export function getRole() {
  const user = getStoredUser();
  return user?.role ?? null;
}

export function isAdmin() {
  return getRole() === "admin";
}
