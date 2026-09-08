import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  User, Mail, Shield, Calendar, Key, Eye, EyeOff,
  Check, Loader2, AlertTriangle, UserCheck,
} from "lucide-react";
import { getMe, changePassword } from "../api/authApi";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import { useI18n } from "../context/I18nContext";

const ROLE_CFG = {
  admin:   { labelKey: "users.role.admin",   bg: "#ede9fe", color: "#6d28d9", border: "#c4b5fd", icon: <Shield size={11} /> },
  analyst: { labelKey: "users.role.analyst", bg: "#dbeafe", color: "#1d4ed8", border: "#93c5fd", icon: <UserCheck size={11} /> },
};

function RoleBadge({ role }) {
  const { t } = useI18n();
  const cfg = ROLE_CFG[role] || ROLE_CFG.analyst;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "3px 10px", borderRadius: 12, fontSize: 11.5, fontWeight: 700,
      background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
    }}>
      {cfg.icon} {t(cfg.labelKey)}
    </span>
  );
}

function InfoRow({ icon, label, value }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 14,
      padding: "13px 0", borderBottom: "1px solid var(--surface-3,#eef1f7)",
    }}>
      <div style={{
        width: 36, height: 36, borderRadius: 9, flexShrink: 0,
        background: "linear-gradient(135deg, rgba(99,102,241,0.12), rgba(14,165,233,0.12))",
        display: "flex", alignItems: "center", justifyContent: "center",
        color: "#6366f1",
      }}>
        {icon}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 11, fontWeight: 700, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 2 }}>
          {label}
        </div>
        <div style={{ fontSize: 13.5, color: "var(--text-primary)", fontWeight: 500 }}>
          {value || "—"}
        </div>
      </div>
    </div>
  );
}

function PasswordSection() {
  const { t } = useI18n();
  const [current, setCurrent]   = useState("");
  const [next,    setNext]      = useState("");
  const [confirm, setConfirm]   = useState("");
  const [showPw,  setShowPw]    = useState(false);
  const [loading, setLoading]   = useState(false);
  const [error,   setError]     = useState(null);
  const [success, setSuccess]   = useState(false);
  const toast = useToast();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (next !== confirm) { setError(t("profile.pwd.mismatch")); return; }
    if (next.length < 8)  { setError(t("profile.pwd.short")); return; }
    setLoading(true);
    try {
      await changePassword(current, next);
      setSuccess(true);
      setCurrent(""); setNext(""); setConfirm("");
      toast.success(t("profile.pwd.toast"));
      setTimeout(() => setSuccess(false), 3000);
    } catch (err) {
      setError(err.message || t("profile.pwd.error"));
    } finally {
      setLoading(false);
    }
  };

  const iStyle = {
    width: "100%", padding: "10px 12px 10px 36px",
    background: "var(--surface-2,#f4f6fa)",
    border: "1px solid var(--surface-4,#e4e8f0)",
    borderRadius: 9, outline: "none",
    fontSize: 13, color: "var(--text-primary)",
    transition: "border 150ms, box-shadow 150ms",
  };

  const fields = [
    { labelKey: "profile.current.pwd", val: current, set: setCurrent },
    { labelKey: "profile.new.pwd",     val: next,    set: setNext },
    { labelKey: "profile.confirm.pwd", val: confirm, set: setConfirm,
      err: confirm && confirm !== next },
  ];

  return (
    <form onSubmit={handleSubmit}>
      {error && (
        <div style={{
          padding: "9px 13px", borderRadius: 8, marginBottom: 14,
          background: "#fef2f2", border: "1px solid #fca5a5",
          fontSize: 12.5, color: "#dc2626",
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <AlertTriangle size={13} /> {error}
        </div>
      )}
      {success && (
        <div style={{
          padding: "9px 13px", borderRadius: 8, marginBottom: 14,
          background: "#f0fdf4", border: "1px solid #86efac",
          fontSize: 12.5, color: "#16a34a",
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <Check size={13} /> {t("profile.pwd.success")}
        </div>
      )}

      {fields.map(({ labelKey, val, set, err: fieldErr }) => (
        <div key={labelKey} style={{ marginBottom: 12 }}>
          <label style={{ display: "block", fontSize: 11.5, fontWeight: 700, color: "var(--text-tertiary)", marginBottom: 5, letterSpacing: "0.05em", textTransform: "uppercase" }}>
            {t(labelKey)}
          </label>
          <div style={{ position: "relative" }}>
            <Key size={13} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-tertiary)", pointerEvents: "none" }} />
            <input
              type={showPw ? "text" : "password"}
              value={val} onChange={e => set(e.target.value)}
              required minLength={labelKey === "profile.current.pwd" ? 1 : 8}
              style={{ ...iStyle, paddingRight: 36, borderColor: fieldErr ? "#fca5a5" : undefined }}
              onFocus={e => { e.target.style.borderColor = "#6366f1"; e.target.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.1)"; }}
              onBlur={e => { e.target.style.borderColor = "var(--surface-4,#e4e8f0)"; e.target.style.boxShadow = "none"; }}
            />
          </div>
        </div>
      ))}

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 4, marginBottom: 16 }}>
        <button
          type="button"
          onClick={() => setShowPw(v => !v)}
          style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: "none", cursor: "pointer", fontSize: 12, color: "var(--text-tertiary)" }}
        >
          {showPw ? <EyeOff size={13} /> : <Eye size={13} />}
          {showPw ? t("profile.hide.pwd") : t("profile.show.pwd")}
        </button>
      </div>

      <button
        type="submit" disabled={loading}
        style={{
          display: "flex", alignItems: "center", gap: 7,
          padding: "10px 20px", borderRadius: 9, cursor: loading ? "not-allowed" : "pointer",
          background: loading ? "rgba(99,102,241,0.5)" : "linear-gradient(135deg, #4f46e5, #0284c7)",
          border: "none", color: "#fff", fontSize: 13, fontWeight: 700,
          boxShadow: loading ? "none" : "0 3px 12px rgba(79,70,229,0.35)",
          transition: "all 150ms",
          opacity: loading ? 0.7 : 1,
        }}
      >
        {loading
          ? <><Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> {t("profile.saving")}</>
          : <><Check size={14} /> {t("profile.change.btn")}</>
        }
      </button>
    </form>
  );
}

export default function Profile() {
  const { t, lang } = useI18n();
  const { user: authUser } = useAuth();
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getMe()
      .then(setProfile)
      .catch(() => setProfile(authUser))
      .finally(() => setLoading(false));
  }, [authUser]);

  const u = profile || authUser;

  if (loading) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", padding: 80 }}>
      <Loader2 size={28} style={{ animation: "spin 1s linear infinite", color: "#6366f1" }} />
    </div>
  );

  const initials = (u?.username || u?.email || "U").slice(0, 2).toUpperCase();
  const locale = lang === "fr" ? "fr-FR" : "en-US";
  const createdAt = u?.created_at
    ? new Date(u.created_at).toLocaleDateString(locale, { day: "2-digit", month: "long", year: "numeric" })
    : null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      style={{ maxWidth: 720, margin: "0 auto", padding: "28px 0" }}
    >
      {/* Header card */}
      <div style={{
        background: "var(--surface-1,#fff)",
        border: "1px solid var(--surface-3,#eef1f7)",
        borderRadius: 16, overflow: "hidden",
        boxShadow: "0 2px 12px rgba(0,0,0,0.04)",
        marginBottom: 20,
      }}>
        {/* Banner */}
        <div style={{
          height: 80,
          background: "linear-gradient(135deg, #3730a3 0%, #4f46e5 40%, #0284c7 80%, #0ea5e9 100%)",
          position: "relative",
        }}>
          <div style={{
            position: "absolute", inset: 0, opacity: 0.12,
            backgroundImage: "repeating-linear-gradient(45deg, transparent, transparent 10px, rgba(255,255,255,0.1) 10px, rgba(255,255,255,0.1) 11px)",
          }} />
        </div>

        {/* Avatar + info */}
        <div style={{ padding: "0 28px 24px", position: "relative" }}>
          <div style={{
            width: 72, height: 72, borderRadius: "50%",
            background: "linear-gradient(135deg, #6366f1, #0ea5e9)",
            border: "3px solid var(--surface-1,#fff)",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 24, fontWeight: 800, color: "#fff",
            position: "relative", top: -36, marginBottom: -20,
            boxShadow: "0 4px 20px rgba(99,102,241,0.35)",
          }}>
            {initials}
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
            <div>
              <div style={{ fontSize: 20, fontWeight: 800, color: "var(--text-primary)", letterSpacing: "-0.02em" }}>
                {u?.username || u?.email?.split("@")[0] || "Utilisateur"}
              </div>
              <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginTop: 2 }}>
                {u?.email}
              </div>
            </div>
            <RoleBadge role={u?.role} />
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 20, alignItems: "start" }}>

        {/* Left — Account info */}
        <div style={{
          background: "var(--surface-1,#fff)",
          border: "1px solid var(--surface-3,#eef1f7)",
          borderRadius: 14, padding: "20px 24px",
          boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
        }}>
          <div style={{ fontSize: 12, fontWeight: 800, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>
            {t("profile.account.info")}
          </div>

          <InfoRow icon={<User size={16} />}   label={t("profile.username")} value={u?.username} />
          <InfoRow icon={<Mail size={16} />}   label={t("profile.email")}    value={u?.email} />
          <InfoRow icon={<Shield size={16} />} label={t("profile.role")}     value={t(ROLE_CFG[u?.role]?.labelKey || "users.role.analyst")} />
          {createdAt && (
            <InfoRow icon={<Calendar size={16} />}  label={t("profile.member.since")} value={createdAt} />
          )}
          {u?.invited_by_username && (
            <InfoRow icon={<UserCheck size={16} />} label={t("profile.invited.by")} value={u.invited_by_username} />
          )}
        </div>

        {/* Right — Change password */}
        <div style={{
          background: "var(--surface-1,#fff)",
          border: "1px solid var(--surface-3,#eef1f7)",
          borderRadius: 14, padding: "20px 24px",
          boxShadow: "0 2px 8px rgba(0,0,0,0.03)",
        }}>
          <div style={{ fontSize: 12, fontWeight: 800, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 16 }}>
            {t("profile.change.pwd")}
          </div>
          <PasswordSection />
        </div>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </motion.div>
  );
}
