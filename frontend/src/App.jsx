import { Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout/Layout";
import ProtectedRoute from "./components/auth/ProtectedRoute";
import Login from "./pages/Login";
import Register from "./pages/Register";
import AcceptInvite from "./pages/AcceptInvite";
import Dashboard from "./pages/Dashboard";
import CreateMigration from "./pages/CreateMigration";
import MigrationDetail from "./pages/MigrationDetail";
import Settings from "./pages/Settings";
import ActivityLog from "./pages/ActivityLog";
import AIAssistant from "./pages/AIAssistant";
import Analytics from "./pages/Analytics";
import UserManagement from "./pages/UserManagement";
import Profile from "./pages/Profile";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <Routes>
      {/* Public routes — outside Layout, no auth required */}
      <Route path="/login"           element={<Login />} />
      <Route path="/register"        element={<Register />} />
      <Route path="/invite/:token"   element={<AcceptInvite />} />

      {/* Protected routes — require authentication */}
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard"      element={<Dashboard />} />
          <Route path="/migrations/new" element={<CreateMigration />} />
          <Route path="/migrations/:id" element={<MigrationDetail />} />
          <Route path="/activity"       element={<ActivityLog />} />
          <Route path="/analytics"      element={<Analytics />} />
          <Route path="/assistant"      element={<AIAssistant />} />
          <Route path="/settings"       element={<Settings />} />

          {/* Profile — accessible to all authenticated users */}
          <Route path="/profile" element={<Profile />} />

          {/* Admin-only route */}
          <Route element={<ProtectedRoute requiredRole="admin" />}>
            <Route path="/admin/users" element={<UserManagement />} />
          </Route>
        </Route>
      </Route>

      {/* 404 — catch all */}
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
