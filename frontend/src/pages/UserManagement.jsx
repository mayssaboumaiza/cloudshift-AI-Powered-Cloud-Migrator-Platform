import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Users, PlusCircle, Trash2, RefreshCw, Shield, Edit3,
  CheckCircle, XCircle, Save, X, Key, UserCheck, UserX,
  Info, Lock, Mail, Clock, Send, Eye, EyeOff,
} from "lucide-react";
import { listUsers, createUser, updateUser, deleteUser, inviteUser, listInvitations, revokeInvitation } from "../api/authApi";
import { useAuth } from "../context/AuthContext";
import { useI18n } from "../context/I18nContext";

/* ── Role config ─────────────────────────────────────────────────────────── */
const ROLE_STYLES = {
  admin:    { bg: "#F5F3FF", color: "#7C3AED", border: "#DDD6FE" },
  analyst:  { bg: "#EFF6FF", color: "#1D6FD1", border: "#BFDBFE" },
  engineer: { bg: "#EFF6FF", color: "#1D6FD1", border: "#BFDBFE" },
  viewer:   { bg: "#F0FDF4", color: "#15803D", border: "#BBF7D0" },
};

const ROLE_LABEL_KEYS = {
  admin:    "users.role.admin",
  analyst:  "users.role.analyst",
  engineer: "users.role.analyst",
  viewer:   "users.role.viewer",
};

function RoleBadge({ role }) {
  const { t } = useI18n();
  const s = ROLE_STYLES[role] || ROLE_STYLES.viewer;
  const labelKey = ROLE_LABEL_KEYS[role] || "users.role.viewer";
  return (
    <span style={{
      padding: "2px 9px", borderRadius: 20, fontSize: 11, fontWeight: 700,
      background: s.bg, color: s.color, border: `1px solid ${s.border}`,
    }}>
      {t(labelKey)}
    </span>
  );
}

const btnStyle = {
  background: "transparent", border: "1px solid var(--surface-3)",
  cursor: "pointer", padding: "5px 8px", borderRadius: 7,
  display: "flex", alignItems: "center", transition: "background 120ms",
};

/* ── User row ────────────────────────────────────────────────────────────── */
function UserRow({ user, currentUserId, onEdit, onDelete, onToggleActive, onResetPassword }) {
  const { t, lang } = useI18n();
  const isSelf = user.id === currentUserId;
  const locale = lang === "fr" ? "fr-FR" : "en-US";
  return (
    <tr style={{ borderTop: "1px solid var(--surface-3)", background: isSelf ? "rgba(99,102,241,0.03)" : "transparent" }}>
      <td style={{ padding: "11px 16px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 32, height: 32, borderRadius: "50%", flexShrink: 0,
            background: "linear-gradient(135deg, #6366f1, #0ea5e9)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 13, fontWeight: 700, color: "#fff",
          }}>
            {(user.username || user.email || "?")[0].toUpperCase()}
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 6 }}>
              {user.username}
              {isSelf && (
                <span style={{ fontSize: 9.5, fontWeight: 700, padding: "1px 6px", borderRadius: 8, background: "#6366f1", color: "#fff" }}>
                  {t("users.you")}
                </span>
              )}
            </div>
            <div style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>{user.email}</div>
          </div>
        </div>
      </td>
      <td style={{ padding: "11px 14px", textAlign: "center" }}>
        <RoleBadge role={user.role} />
      </td>
      <td style={{ padding: "11px 14px", textAlign: "center" }}>
        {user.is_active
          ? <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11.5, fontWeight: 600, color: "#16a34a" }}>
              <CheckCircle size={13} /> {t("users.active")}
            </span>
          : <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11.5, fontWeight: 600, color: "#dc2626" }}>
              <XCircle size={13} /> {t("users.disabled")}
            </span>
        }
      </td>
      <td style={{ padding: "11px 14px", textAlign: "center", fontSize: 11.5, color: "var(--text-tertiary)" }}>
        {new Date(user.created_at).toLocaleDateString(locale)}
      </td>
      <td style={{ padding: "11px 16px" }}>
        <div style={{ display: "flex", gap: 4, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <button
            onClick={() => onEdit(user)}
            title={t("users.edit.role")}
            style={{ ...btnStyle, color: "#6366f1" }}
            onMouseEnter={e => e.currentTarget.style.background = "#f0f0ff"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          >
            <Edit3 size={13} />
          </button>
          <button
            onClick={() => onResetPassword(user)}
            title={t("users.reset.pwd.btn")}
            style={{ ...btnStyle, color: "#d97706" }}
            onMouseEnter={e => e.currentTarget.style.background = "#fffbeb"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          >
            <Key size={13} />
          </button>
          {!isSelf && (
            <button
              onClick={() => onToggleActive(user)}
              title={user.is_active ? t("users.deactivate") : t("users.activate")}
              style={{ ...btnStyle, color: user.is_active ? "#d97706" : "#16a34a" }}
              onMouseEnter={e => e.currentTarget.style.background = user.is_active ? "#fffbeb" : "#f0fdf4"}
              onMouseLeave={e => e.currentTarget.style.background = "transparent"}
            >
              {user.is_active ? <UserX size={13} /> : <UserCheck size={13} />}
            </button>
          )}
          {!isSelf && (
            <button
              onClick={() => onDelete(user)}
              title={t("users.delete")}
              style={{ ...btnStyle, color: "#dc2626" }}
              onMouseEnter={e => e.currentTarget.style.background = "#fef2f2"}
              onMouseLeave={e => e.currentTarget.style.background = "transparent"}
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

// ── Shared modal constants ────────────────────────────────────────────────────

const ROLE_META = {
  admin:   { color: "#7c3aed", bg: "#f5f3ff", border: "#ddd6fe", icon: Shield },
  analyst: { color: "#0891b2", bg: "#ecfeff", border: "#a5f3fc", icon: Users  },
};

const MODAL_OVERLAY = {
  position: "fixed", inset: 0, zIndex: 500,
  background: "rgba(15,23,42,0.65)",
  backdropFilter: "blur(5px)",
  display: "flex", alignItems: "center", justifyContent: "center",
  padding: 24,
};

const MODAL_ANIM = {
  initial:    { opacity: 0, scale: 0.95, y: 16 },
  animate:    { opacity: 1, scale: 1,    y: 0  },
  exit:       { opacity: 0, scale: 0.95, y: 16 },
  transition: { duration: 0.2, ease: "easeOut" },
};

function ModalShell({ width = 480, onClose, header, children }) {
  const handleKey = useCallback((e) => { if (e.key === "Escape") onClose(); }, [onClose]);
  useEffect(() => {
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKey);
    return () => { document.body.style.overflow = ""; window.removeEventListener("keydown", handleKey); };
  }, [handleKey]);

  return (
    <div style={MODAL_OVERLAY} onClick={onClose}>
      <motion.div
        {...MODAL_ANIM}
        onClick={e => e.stopPropagation()}
        style={{
          width: "100%", maxWidth: width,
          maxHeight: "90vh",
          background: "#fff", borderRadius: 20,
          boxShadow: "0 28px 64px rgba(0,0,0,0.22)",
          border: "1px solid #e2e8f0",
          overflow: "hidden", display: "flex", flexDirection: "column",
        }}
      >
        {header}
        {children}
      </motion.div>
    </div>
  );
}

function ModalHeader({ icon: Icon, iconColor, iconBg, title, subtitle, onClose }) {
  return (
    <div style={{
      background: `linear-gradient(135deg, ${iconBg} 0%, #fff 100%)`,
      borderBottom: "1px solid #e2e8f0",
      padding: "22px 24px 18px",
      display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{
          width: 44, height: 44, borderRadius: 12, flexShrink: 0,
          background: iconColor, display: "flex", alignItems: "center", justifyContent: "center",
          boxShadow: `0 4px 12px ${iconColor}44`,
        }}>
          <Icon size={20} color="#fff" />
        </div>
        <div>
          <div style={{ fontWeight: 700, fontSize: 16, color: "#0f172a", lineHeight: 1.2 }}>{title}</div>
          {subtitle && <div style={{ fontSize: 12, color: "#64748b", marginTop: 3 }}>{subtitle}</div>}
        </div>
      </div>
      <button
        onClick={onClose}
        style={{
          width: 30, height: 30, borderRadius: "50%", flexShrink: 0,
          border: "1px solid #e2e8f0", background: "#f8fafc",
          cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
          color: "#94a3b8", transition: "all 0.15s",
        }}
        onMouseOver={e => { e.currentTarget.style.background = "#fee2e2"; e.currentTarget.style.color = "#dc2626"; }}
        onMouseOut={e  => { e.currentTarget.style.background = "#f8fafc"; e.currentTarget.style.color = "#94a3b8"; }}
      >
        <X size={14} />
      </button>
    </div>
  );
}

function RoleSelector({ value, onChange, namePrefix }) {
  const { t } = useI18n();
  const OPTIONS = [
    { value: "admin",   labelKey: "users.role.admin",   descKey: "users.role.admin.desc"   },
    { value: "analyst", labelKey: "users.role.analyst",  descKey: "users.role.analyst.desc" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {OPTIONS.map(r => {
        const meta    = ROLE_META[r.value] || ROLE_META.analyst;
        const Icon    = meta.icon;
        const active  = value === r.value;
        return (
          <label key={r.value} style={{
            display: "flex", alignItems: "flex-start", gap: 12,
            padding: "12px 14px", borderRadius: 12, cursor: "pointer",
            border: `2px solid ${active ? meta.color : "#e2e8f0"}`,
            background: active ? meta.bg : "#fafafa",
            transition: "all 0.15s",
          }}>
            <input
              type="radio" name={namePrefix} value={r.value}
              checked={active} onChange={() => onChange(r.value)}
              style={{ display: "none" }}
            />
            <div style={{
              width: 32, height: 32, borderRadius: 8, flexShrink: 0,
              background: active ? meta.color : "#e2e8f0",
              display: "flex", alignItems: "center", justifyContent: "center",
              transition: "all 0.15s",
            }}>
              <Icon size={14} color={active ? "#fff" : "#94a3b8"} />
            </div>
            <div style={{ flex: 1 }}>
              <div style={{
                fontSize: 13, fontWeight: 700,
                color: active ? meta.color : "#1e293b",
                marginBottom: 2,
              }}>
                {t(r.labelKey)}
              </div>
              <div style={{ fontSize: 11.5, color: "#64748b", lineHeight: 1.4 }}>
                {t(r.descKey)}
              </div>
            </div>
            {active && (
              <CheckCircle size={16} color={meta.color} style={{ flexShrink: 0, marginTop: 2 }} />
            )}
          </label>
        );
      })}
    </div>
  );
}

function PasswordField({ value, onChange, required, placeholder, label, hint }) {
  const [show, setShow] = useState(false);
  return (
    <div className="form-group">
      {label && <label className="form-label">{label}</label>}
      <div style={{ position: "relative" }}>
        <Lock size={14} style={{
          position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)",
          color: "#94a3b8", pointerEvents: "none",
        }} />
        <input
          className="form-input"
          type={show ? "text" : "password"}
          value={value}
          onChange={e => onChange(e.target.value)}
          required={required}
          minLength={required ? 8 : 0}
          placeholder={placeholder}
          style={{ paddingLeft: 36, paddingRight: 40 }}
        />
        <button
          type="button"
          onClick={() => setShow(v => !v)}
          style={{
            position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
            background: "none", border: "none", cursor: "pointer",
            color: "#94a3b8", padding: 2, display: "flex",
          }}
        >
          {show ? <EyeOff size={14} /> : <Eye size={14} />}
        </button>
      </div>
      {hint && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 5 }}>{hint}</div>}
    </div>
  );
}

function FormInput({ label, hint, ...props }) {
  return (
    <div className="form-group">
      {label && <label className="form-label">{label}</label>}
      <input className="form-input" {...props} />
      {hint && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 5 }}>{hint}</div>}
    </div>
  );
}

function ErrorBanner({ message }) {
  if (!message) return null;
  return (
    <div style={{
      display: "flex", alignItems: "flex-start", gap: 8,
      padding: "10px 14px", borderRadius: 10, marginBottom: 16,
      background: "#fef2f2", border: "1px solid #fecaca",
      fontSize: 12.5, color: "#dc2626",
    }}>
      <XCircle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
      {message}
    </div>
  );
}

function ModalFooter({ onClose, loading, submitLabel, cancelLabel, submitIcon: SubmitIcon, danger = false }) {
  const color = danger ? "#dc2626" : "#6366f1";
  return (
    <div style={{
      borderTop: "1px solid #e2e8f0", padding: "16px 24px",
      background: "#f8fafc",
      display: "flex", justifyContent: "flex-end", gap: 10,
    }}>
      <button
        type="button"
        onClick={onClose}
        disabled={loading}
        style={{
          padding: "9px 18px", borderRadius: 10,
          border: "1px solid #e2e8f0", background: "#fff",
          fontSize: 13, fontWeight: 600, color: "#475569", cursor: "pointer",
        }}
      >
        {cancelLabel}
      </button>
      <button
        type="submit"
        disabled={loading}
        style={{
          padding: "9px 20px", borderRadius: 10, border: "none",
          background: loading ? "#e2e8f0" : `linear-gradient(135deg, ${color}, ${color}cc)`,
          color: loading ? "#94a3b8" : "#fff",
          fontSize: 13, fontWeight: 700, cursor: loading ? "not-allowed" : "pointer",
          display: "flex", alignItems: "center", gap: 7,
          boxShadow: loading ? "none" : `0 4px 12px ${color}44`,
          transition: "all 0.2s",
        }}
      >
        {loading
          ? <RefreshCw size={13} style={{ animation: "um-spin 0.8s linear infinite" }} />
          : SubmitIcon && <SubmitIcon size={13} />
        }
        {submitLabel}
      </button>
    </div>
  );
}

// ── Invite modal ──────────────────────────────────────────────────────────────

function InviteModal({ onClose, onSent }) {
  const { t } = useI18n();
  const [email,   setEmail]   = useState("");
  const [role,    setRole]    = useState("analyst");
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null); setLoading(true);
    try   { await inviteUser(email, role); onSent(email); }
    catch (err) { setError(err.message); }
    finally     { setLoading(false); }
  };

  return (
    <ModalShell width={480} onClose={onClose} header={
      <ModalHeader
        icon={Send} iconColor="#6366f1" iconBg="#eef2ff"
        title={t("users.invite.title")}
        subtitle={t("users.invite.email.hint")}
        onClose={onClose}
      />
    }>
      <form onSubmit={handleSubmit}>
        <div style={{ padding: "22px 24px", display: "flex", flexDirection: "column", gap: 14, overflowY: "auto" }}>
          <ErrorBanner message={error} />
          <FormInput
            label={t("users.invite.email")}
            type="email" required autoFocus
            value={email} onChange={e => setEmail(e.target.value)}
            placeholder="colleague@company.com"
          />
          <div className="form-group">
            <label className="form-label">{t("users.invite.role")}</label>
            <RoleSelector value={role} onChange={setRole} namePrefix="invite-role" />
          </div>
        </div>
        <ModalFooter
          onClose={onClose} loading={loading}
          submitLabel={t("users.invite.send")}
          cancelLabel={t("users.modal.cancel")}
          submitIcon={Send}
        />
      </form>
    </ModalShell>
  );
}

// ── Create / Edit modal ───────────────────────────────────────────────────────

function UserModal({ user, onClose, onSaved }) {
  const { t } = useI18n();
  const isNew = !user;

  const [form, setForm] = useState({
    email:     user?.email     ?? "",
    username:  user?.username  ?? "",
    password:  "",
    role:      user?.role      ?? "analyst",
    is_active: user?.is_active ?? true,
  });
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  const meta = ROLE_META[form.role] || ROLE_META.analyst;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null); setLoading(true);
    try {
      if (isNew) {
        await createUser({ ...form });
      } else {
        const updates = {};
        if (form.role      !== user.role)      updates.role      = form.role;
        if (form.is_active !== user.is_active) updates.is_active = form.is_active;
        if (form.password)                     updates.password  = form.password;
        if (Object.keys(updates).length)       await updateUser(user.id, updates);
      }
      onSaved();
    } catch (err) { setError(err.message); }
    finally       { setLoading(false); }
  };

  return (
    <ModalShell width={500} onClose={onClose} header={
      <ModalHeader
        icon={isNew ? PlusCircle : Edit3}
        iconColor={meta.color} iconBg={meta.bg}
        title={isNew ? t("users.modal.create") : t("users.modal.edit").replace("{name}", user.username)}
        subtitle={isNew ? t("users.modal.pwd.hint") : t("users.edit.role")}
        onClose={onClose}
      />
    }>
      <form onSubmit={handleSubmit}>
        <div style={{ padding: "22px 24px", display: "flex", flexDirection: "column", gap: 14, overflowY: "auto" }}>
          <ErrorBanner message={error} />

          {isNew && (
            <>
              <FormInput
                label={t("users.modal.email")}
                type="email" required autoFocus
                value={form.email}
                onChange={e => setForm(p => ({ ...p, email: e.target.value }))}
                placeholder="prenom.nom@entreprise.com"
              />
              <FormInput
                label={t("users.modal.username")}
                required minLength={3}
                value={form.username}
                onChange={e => setForm(p => ({ ...p, username: e.target.value }))}
                placeholder="prenom.nom"
              />
            </>
          )}

          <div className="form-group">
            <label className="form-label">{t("users.invite.role")}</label>
            <RoleSelector
              value={form.role}
              onChange={v => setForm(p => ({ ...p, role: v }))}
              namePrefix="user-role"
            />
          </div>

          <PasswordField
            label={isNew ? t("users.modal.password") : t("users.modal.pwd.edit")}
            value={form.password}
            onChange={v => setForm(p => ({ ...p, password: v }))}
            required={isNew}
            placeholder={isNew ? t("users.min.chars").replace("{n}", "8") : t("users.unchanged")}
            hint={isNew ? t("users.modal.pwd.hint") : null}
          />
        </div>

        <ModalFooter
          onClose={onClose} loading={loading}
          submitLabel={isNew ? t("users.modal.create.btn") : t("users.modal.save")}
          cancelLabel={t("users.modal.cancel")}
          submitIcon={isNew ? PlusCircle : Save}
        />
      </form>
    </ModalShell>
  );
}

// ── Reset password modal ──────────────────────────────────────────────────────

function ResetPasswordModal({ user, onClose, onSaved }) {
  const { t } = useI18n();
  const [password, setPassword] = useState("");
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (password.length < 8) { setError(t("users.reset.min")); return; }
    setError(null); setLoading(true);
    try   { await updateUser(user.id, { password }); onSaved(); }
    catch (err) { setError(err.message); }
    finally     { setLoading(false); }
  };

  return (
    <ModalShell width={420} onClose={onClose} header={
      <ModalHeader
        icon={Key} iconColor="#d97706" iconBg="#fffbeb"
        title={t("users.reset.title").replace("{name}", user.username)}
        subtitle={t("users.reset.new.pwd")}
        onClose={onClose}
      />
    }>
      <form onSubmit={handleSubmit}>
        <div style={{ padding: "22px 24px", overflowY: "auto" }}>
          <ErrorBanner message={error} />

          {/* User info chip */}
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 14px", borderRadius: 10,
            background: "#f8fafc", border: "1px solid #e2e8f0",
            marginBottom: 16,
          }}>
            <div style={{
              width: 34, height: 34, borderRadius: "50%", flexShrink: 0,
              background: "linear-gradient(135deg,#6366f1,#0ea5e9)",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 14, fontWeight: 700, color: "#fff",
            }}>
              {(user.username || "?")[0].toUpperCase()}
            </div>
            <div>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#0f172a" }}>{user.username}</div>
              <div style={{ fontSize: 11.5, color: "#64748b" }}>{user.email}</div>
            </div>
          </div>

          <PasswordField
            label={t("users.reset.new.pwd")}
            value={password}
            onChange={setPassword}
            required
            placeholder={t("users.min.chars").replace("{n}", "8")}
            hint={t("users.modal.pwd.hint")}
          />
        </div>

        <ModalFooter
          onClose={onClose} loading={loading}
          submitLabel={t("users.reset.pwd")}
          cancelLabel={t("users.modal.cancel")}
          submitIcon={Key}
          danger
        />
      </form>
    </ModalShell>
  );
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */
function hoursUntil(dateStr, t) {
  const diff = new Date(dateStr) - Date.now();
  const h = Math.floor(diff / 3600000);
  if (h <= 0) return t("users.expired");
  if (h < 24) return t("users.hours.left").replace("{h}", h).replace("{s}", h > 1 ? "s" : "");
  const d = Math.floor(h / 24);
  return t("users.days.left").replace("{d}", d).replace("{h}", h % 24);
}

/* ── Main component ──────────────────────────────────────────────────────── */
export default function UserManagement() {
  const { t } = useI18n();
  const { user: currentUser } = useAuth();
  const [users,         setUsers]         = useState([]);
  const [invitations,   setInvitations]   = useState([]);
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState(null);
  const [success,       setSuccess]       = useState(null);
  const [modal,         setModal]         = useState(null);
  const [resetModal,    setResetModal]    = useState(null);
  const [inviteModal,   setInviteModal]   = useState(false);
  const [showGuide,     setShowGuide]     = useState(false);

  const load = () => {
    setLoading(true); setError(null);
    Promise.all([listUsers(), listInvitations()])
      .then(([u, inv]) => { setUsers(u); setInvitations(inv); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const handleDelete = async (user) => {
    if (!confirm(t("users.delete.confirm2").replace("{name}", user.username))) return;
    try {
      await deleteUser(user.id);
      setUsers(prev => prev.filter(u => u.id !== user.id));
      setSuccess(t("users.deleted2").replace("{name}", user.username));
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) { setError(err.message); }
  };

  const handleToggleActive = async (user) => {
    const action = user.is_active ? t("users.toggle.deactivate") : t("users.toggle.activate");
    if (!confirm(t("users.toggle.confirm").replace("{action}", action).replace("{name}", user.username))) return;
    try {
      await updateUser(user.id, { is_active: !user.is_active });
      setUsers(prev => prev.map(u => u.id === user.id ? { ...u, is_active: !u.is_active } : u));
      const msg = user.is_active ? t("users.toggled.off") : t("users.toggled.on");
      setSuccess(msg.replace("{name}", user.username));
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) { setError(err.message); }
  };

  const handleRevokeInvitation = async (inv) => {
    if (!confirm(t("users.revoke.confirm").replace("{email}", inv.email))) return;
    try {
      await revokeInvitation(inv.id);
      setInvitations(prev => prev.filter(i => i.id !== inv.id));
      setSuccess(t("users.revoked").replace("{email}", inv.email));
      setTimeout(() => setSuccess(null), 3000);
    } catch (err) { setError(err.message); }
  };

  const admins     = users.filter(u => u.role === "admin");
  const engineers  = users.filter(u => u.role !== "admin");
  const activeCount = users.filter(u => u.is_active).length;

  const TABLE_HEADERS = [
    { key: "user",    label: t("users.col.user"),  align: "left"   },
    { key: "role",    label: t("users.role"),       align: "center" },
    { key: "status",  label: t("users.status"),     align: "center" },
    { key: "created", label: t("users.created"),    align: "center" },
    { key: "actions", label: t("users.actions"),    align: "right"  },
  ];

  return (
    <motion.div className="page" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>

      {/* Header */}
      <div className="page-header">
        <div>
          <h2 className="page-title" style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <Shield size={20} style={{ color: "#7C3AED" }} />
            {t("users.title")}
          </h2>
          <p className="page-subtitle">
            {loading
              ? t("users.loading")
              : t("users.subtitle.count")
                  .replace("{n}", users.length)
                  .replace("{s}", users.length !== 1 ? "s" : "")
                  .replace("{a}", activeCount)
                  .replace("{as}", activeCount !== 1 ? "s" : "")
            }
          </p>
        </div>
        <div className="page-actions">
          <button
            onClick={() => setShowGuide(v => !v)}
            className="btn btn-secondary"
            title={t("users.guide")}
          >
            <Info size={14} /> {t("users.guide")}
          </button>
          <button className="btn btn-secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} style={{ animation: loading ? "spin 1s linear infinite" : "none" }} />
          </button>
          <button className="btn btn-primary" onClick={() => setInviteModal(true)}>
            <Send size={14} /> {t("users.invite")}
          </button>
          <button className="btn btn-secondary" onClick={() => setModal("create")}>
            <PlusCircle size={14} /> {t("users.create.direct")}
          </button>
        </div>
      </div>

      {/* Developer guide banner */}
      <AnimatePresence>
        {showGuide && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            style={{ overflow: "hidden", marginBottom: 18 }}
          >
            <div style={{
              padding: "18px 20px", borderRadius: 12,
              background: "linear-gradient(135deg, #f5f3ff, #eff6ff)",
              border: "1px solid #c4b5fd",
            }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "#5b21b6", marginBottom: 12, display: "flex", alignItems: "center", gap: 7 }}>
                <Shield size={14} /> Guide
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px,1fr))", gap: 14 }}>
                {[
                  { step: "1", title: t("users.role.admin"),   desc: t("users.role.admin.desc"),   color: "#7c3aed" },
                  { step: "2", title: t("users.role.analyst"),  desc: t("users.role.analyst.desc"), color: "#1d6fd1" },
                  { step: "3", title: t("users.deactivate"),   desc: t("users.toggled.off").replace("{name}", "…"),  color: "#0891b2" },
                  { step: "4", title: t("users.delete"),       desc: t("users.delete.confirm2").replace("{name}", "…"), color: "#15803d" },
                ].map(item => (
                  <div key={item.step} style={{
                    padding: "12px 14px", borderRadius: 9,
                    background: "#fff", border: `1px solid ${item.color}22`,
                    borderLeft: `3px solid ${item.color}`,
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}>
                      <span style={{
                        width: 20, height: 20, borderRadius: "50%", background: item.color,
                        color: "#fff", fontSize: 11, fontWeight: 800,
                        display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
                      }}>
                        {item.step}
                      </span>
                      <span style={{ fontSize: 12.5, fontWeight: 700, color: item.color }}>{item.title}</span>
                    </div>
                    <p style={{ margin: 0, fontSize: 11.5, color: "var(--text-secondary)", lineHeight: 1.55 }}>{item.desc}</p>
                  </div>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Stats bar */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px,1fr))", gap: 10, marginBottom: 18 }}>
        {[
          { labelKey: "users.stat.total",     value: users.length,    color: "#6366f1", icon: "👥" },
          { labelKey: "users.stat.admins",    value: admins.length,   color: "#7c3aed", icon: "🛡" },
          { labelKey: "users.stat.engineers", value: engineers.length, color: "#1d6fd1", icon: "⚙️" },
          { labelKey: "users.stat.active",    value: activeCount,     color: "#16a34a", icon: "✅" },
        ].map(s => (
          <div key={s.labelKey} style={{
            padding: "12px 16px", borderRadius: 10,
            background: "#fff", border: "1px solid var(--surface-4)",
            display: "flex", alignItems: "center", gap: 10,
            boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}>
            <span style={{ fontSize: 20 }}>{s.icon}</span>
            <div>
              <div style={{ fontSize: 22, fontWeight: 800, color: s.color, lineHeight: 1 }}>{s.value}</div>
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>{t(s.labelKey)}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Invitations panel */}
      {invitations.length > 0 && (
        <div style={{ background: "#fff", borderRadius: 12, border: "1px solid var(--surface-4)", overflow: "hidden", boxShadow: "0 1px 4px rgba(0,0,0,0.04)", marginBottom: 18 }}>
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--surface-3)", background: "rgba(99,102,241,0.03)", display: "flex", alignItems: "center", gap: 8 }}>
            <Mail size={14} style={{ color: "#6366f1" }} />
            <span style={{ fontSize: 12.5, fontWeight: 700, color: "#3730a3" }}>{t("users.invite.pending")}</span>
            <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 20, background: "#ede9fe", color: "#6d28d9" }}>{invitations.length}</span>
          </div>
          <div style={{ padding: "8px 8px" }}>
            {invitations.map(inv => (
              <div key={inv.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 12px", borderRadius: 8, marginBottom: 2 }}>
                <div style={{ width: 30, height: 30, borderRadius: "50%", background: "rgba(99,102,241,0.12)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                  <Mail size={13} style={{ color: "#6366f1" }} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{inv.email}</div>
                  <div style={{ fontSize: 11, color: "var(--text-tertiary)", display: "flex", alignItems: "center", gap: 6, marginTop: 1 }}>
                    <RoleBadge role={inv.role} />
                    <span style={{ display: "flex", alignItems: "center", gap: 3 }}><Clock size={10} /> {hoursUntil(inv.expires_at, t)}</span>
                  </div>
                </div>
                <button
                  onClick={() => handleRevokeInvitation(inv)}
                  title={t("users.invite.revoke")}
                  style={{ ...btnStyle, color: "#dc2626", flexShrink: 0 }}
                  onMouseEnter={e => e.currentTarget.style.background = "#fef2f2"}
                  onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Alerts */}
      {error && (
        <div style={{ padding: "10px 14px", borderRadius: 9, marginBottom: 14, background: "var(--error-subtle)", border: "1px solid var(--error-border)", fontSize: 13, color: "var(--error-text)" }}>
          {error}
        </div>
      )}
      {success && (
        <div style={{ padding: "10px 14px", borderRadius: 9, marginBottom: 14, background: "var(--success-subtle)", border: "1px solid var(--success-border)", fontSize: 13, color: "var(--success-text)", display: "flex", alignItems: "center", gap: 8 }}>
          <CheckCircle size={14} /> {success}
        </div>
      )}

      {/* Users table */}
      <div style={{
        background: "#fff", borderRadius: 12,
        border: "1px solid var(--surface-4)", overflow: "hidden",
        boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
      }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr style={{ background: "var(--surface-1)" }}>
              {TABLE_HEADERS.map(h => (
                <th key={h.key} style={{
                  padding: "11px 16px",
                  textAlign: h.align,
                  fontSize: 10.5, fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.07em", color: "var(--text-tertiary)",
                  borderBottom: "1px solid var(--surface-3)",
                }}>{h.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading
              ? [...Array(3)].map((_, i) => (
                  <tr key={i}>
                    {[...Array(5)].map((_, j) => (
                      <td key={j} style={{ padding: "11px 16px" }}>
                        <div style={{ height: 14, borderRadius: 4, background: "var(--surface-3)", animation: "skeleton-pulse 1.5s ease-in-out infinite", animationDelay: `${(i*5+j)*0.04}s` }} />
                      </td>
                    ))}
                  </tr>
                ))
              : users.length === 0
                ? (
                  <tr>
                    <td colSpan={5} style={{ padding: "40px", textAlign: "center", color: "var(--text-tertiary)" }}>
                      <Users size={32} style={{ margin: "0 auto 10px", display: "block", opacity: 0.25 }} />
                      <div style={{ fontSize: 14 }}>{t("users.empty.title")}</div>
                      <div style={{ fontSize: 12, marginTop: 4 }}>{t("users.empty.desc")}</div>
                    </td>
                  </tr>
                )
                : users.map(u => (
                  <UserRow
                    key={u.id} user={u}
                    currentUserId={currentUser?.id}
                    onEdit={u => setModal(u)}
                    onDelete={handleDelete}
                    onToggleActive={handleToggleActive}
                    onResetPassword={u => setResetModal(u)}
                  />
                ))
            }
          </tbody>
        </table>
      </div>

      {/* Modals */}
      <AnimatePresence>
        {inviteModal && (
          <InviteModal
            onClose={() => setInviteModal(false)}
            onSent={(email) => {
              setInviteModal(false);
              setSuccess(t("users.invite.sent").replace("{email}", email));
              setTimeout(() => setSuccess(null), 4000);
              load();
            }}
          />
        )}
        {modal && (
          <UserModal
            user={modal === "create" ? null : modal}
            onClose={() => setModal(null)}
            onSaved={() => { setModal(null); load(); setSuccess(t("users.updated")); setTimeout(() => setSuccess(null), 3000); }}
          />
        )}
        {resetModal && (
          <ResetPasswordModal
            user={resetModal}
            onClose={() => setResetModal(null)}
            onSaved={() => {
              setResetModal(null);
              setSuccess(t("users.reset.done").replace("{name}", resetModal.username));
              setTimeout(() => setSuccess(null), 3000);
            }}
          />
        )}
      </AnimatePresence>

      <style>{`
        @keyframes spin    { to { transform: rotate(360deg); } }
        @keyframes um-spin { to { transform: rotate(360deg); } }
        @keyframes skeleton-pulse {
          0%,100% { opacity: 1; } 50% { opacity: 0.4; }
        }
      `}</style>
    </motion.div>
  );
}
