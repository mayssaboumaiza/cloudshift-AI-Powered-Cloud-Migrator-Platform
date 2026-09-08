/**
 * ProtectedRoute.jsx — Route guard component.
 *
 * NOT wired into App.jsx yet. Usage when ready:
 *
 *   <Route element={<ProtectedRoute />}>
 *     <Route path="/dashboard" element={<Dashboard />} />
 *   </Route>
 *
 *   // Admin-only:
 *   <Route element={<ProtectedRoute requiredRole="admin" />}>
 *     <Route path="/admin/users" element={<UserManagement />} />
 *   </Route>
 *
 * Behaviour:
 *   - Not authenticated → redirect to /login?next=<current path>
 *   - Authenticated but wrong role → show 403 inline
 *   - Loading → spinner
 */
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loader2, ShieldOff } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const ROLE_HIERARCHY = { admin: 3, analyst: 2, viewer: 1 };

function hasRequiredRole(userRole, requiredRole) {
  if (!requiredRole) return true;
  return (ROLE_HIERARCHY[userRole] ?? 0) >= (ROLE_HIERARCHY[requiredRole] ?? 0);
}

export default function ProtectedRoute({ requiredRole = null }) {
  const { user, loading, isLoggedIn } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div style={{
        height: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        flexDirection: "column", gap: 12, color: "var(--text-tertiary)",
      }}>
        <Loader2 size={28} style={{ animation: "spin 1s linear infinite" }} />
        <span style={{ fontSize: 13 }}>Verifying session…</span>
      </div>
    );
  }

  if (!isLoggedIn()) {
    return (
      <Navigate
        to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`}
        replace
      />
    );
  }

  if (!hasRequiredRole(user.role, requiredRole)) {
    return (
      <div style={{
        height: "calc(100vh - var(--topbar-height))",
        display: "flex", alignItems: "center", justifyContent: "center",
        flexDirection: "column", gap: 14,
      }}>
        <ShieldOff size={40} style={{ color: "var(--error-solid)", opacity: 0.7 }} />
        <div style={{ textAlign: "center" }}>
          <div style={{ fontWeight: 700, fontSize: 16, color: "var(--text-primary)" }}>
            Access Denied
          </div>
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginTop: 4 }}>
            This page requires <strong>{requiredRole}</strong> role.
            Your current role is <strong>{user.role}</strong>.
          </div>
        </div>
      </div>
    );
  }

  return <Outlet />;
}
