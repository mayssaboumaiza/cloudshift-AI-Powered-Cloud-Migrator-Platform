/**
 * AcceptInvite.jsx — Page d'acceptation d'une invitation.
 * Route publique : /invite/:token
 */
import { useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { Lock, Eye, EyeOff, Loader2, User, ArrowRight, AlertTriangle } from "lucide-react";
import { getInviteInfo, acceptInvite, getMe } from "../api/authApi";
import { useToast } from "../context/ToastContext";
import { useI18n } from "../context/I18nContext";
import AuthBackground from "../components/auth/AuthBackground";
import AuthHeader from "../components/auth/AuthHeader";
import AuthFooter from "../components/auth/AuthFooter";

const inputStyle = {
  width: "100%", boxSizing: "border-box",
  padding: "11px 14px 11px 36px",
  background: "rgba(255,255,255,0.85)",
  border: "1px solid rgba(99,102,241,0.22)",
  borderRadius: 10, outline: "none",
  color: "#1e3a5f", fontSize: 13.5,
  transition: "border-color 150ms, background 150ms",
};
const labelStyle = {
  display: "block", fontSize: 11.5, fontWeight: 700,
  color: "rgba(30,58,138,0.6)", marginBottom: 6,
  letterSpacing: "0.05em", textTransform: "uppercase",
};

export default function AcceptInvite() {
  const { token } = useParams();
  const navigate  = useNavigate();
  const toast     = useToast();
  const { t }     = useI18n();

  const [inviteInfo,  setInviteInfo]  = useState(null);
  const [loadingInfo, setLoadingInfo] = useState(true);
  const [invalidMsg,  setInvalidMsg]  = useState(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm,  setConfirm]  = useState("");
  const [showPw,   setShowPw]   = useState(false);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState(null);

  useEffect(() => {
    getInviteInfo(token)
      .then(setInviteInfo)
      .catch((err) => setInvalidMsg(err.message || t("invite.invalid.default")))
      .finally(() => setLoadingInfo(false));
  }, [token]);

  const ROLE_LABEL = {
    admin:   t("users.role.admin"),
    analyst: t("users.role.analyst"),
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) { setError(t("invite.mismatch")); return; }
    setLoading(true);
    try {
      await acceptInvite(token, username, password);
      const me = await getMe().catch(() => null);
      const name = me?.username || username;
      const isAdmin = (me?.role || inviteInfo?.role) === "admin";
      toast.success(
        isAdmin ? t("invite.welcome.admin") : t("invite.welcome.engineer"),
        { title: `${t("welcome.title")}, ${name} 👋`, duration: 5500 },
      );
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err.message || t("common.error"));
    } finally {
      setLoading(false);
    }
  };

  const focusStyle = (e) => { e.target.style.borderColor = "rgba(99,102,241,0.75)"; e.target.style.background = "#fff"; e.target.style.boxShadow = "0 0 0 3px rgba(99,102,241,0.12)"; };
  const blurStyle  = (e) => { e.target.style.borderColor = "rgba(99,102,241,0.22)"; e.target.style.background = "rgba(255,255,255,0.85)"; e.target.style.boxShadow = "none"; };

  return (
    <div style={{
      minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "var(--font-ui, system-ui)", position: "relative", overflow: "hidden",
      paddingTop: 54, paddingBottom: 56,
    }}>
      <AuthBackground />
      <AuthHeader />
      <AuthFooter />

      <motion.div
        initial={{ opacity: 0, y: 20, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
        style={{ position: "relative", zIndex: 10, width: "100%", maxWidth: 420, padding: "0 16px" }}
      >
        {/* Brand */}
        <div style={{ textAlign: "center", marginBottom: 30 }}>
          <div style={{ fontSize: 28, fontWeight: 900, letterSpacing: "-0.04em", background: "linear-gradient(135deg, #3730a3 0%, #1d4ed8 50%, #0369a1 100%)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", backgroundClip: "text", marginBottom: 4 }}>
            CloudShift
          </div>
          <div style={{ fontSize: 12, color: "rgba(30,58,138,0.5)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 600 }}>
            {t("invite.subtitle")}
          </div>
        </div>

        <div style={{
          background: "rgba(255,255,255,0.78)", backdropFilter: "blur(22px)",
          borderRadius: 18, border: "1px solid rgba(255,255,255,0.95)",
          boxShadow: "0 24px 64px rgba(99,102,241,0.14), 0 4px 20px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,1)",
          overflow: "hidden",
        }}>
          <div style={{ background: "linear-gradient(135deg, #4f46e5, #0284c7)", padding: "18px 28px" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#fff" }}>{t("invite.create.title")}</div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.75)", marginTop: 2 }}>{t("invite.create.sub")}</div>
          </div>

          <div style={{ padding: "26px 28px 24px" }}>
            {loadingInfo ? (
              <div style={{ textAlign: "center", padding: "32px 0", color: "rgba(30,58,138,0.5)" }}>
                <Loader2 size={24} style={{ animation: "spin 1s linear infinite", margin: "0 auto" }} />
              </div>
            ) : invalidMsg ? (
              <div style={{ padding: "16px", borderRadius: 10, background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", display: "flex", gap: 10, alignItems: "flex-start" }}>
                <AlertTriangle size={18} style={{ color: "#ef4444", flexShrink: 0, marginTop: 1 }} />
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: "#ef4444" }}>{t("invite.invalid.title")}</div>
                  <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>{invalidMsg}</div>
                  <button onClick={() => navigate("/login")} style={{ marginTop: 10, fontSize: 12, color: "#6366f1", background: "none", border: "none", cursor: "pointer", padding: 0, textDecoration: "underline" }}>
                    {t("invite.back.login")}
                  </button>
                </div>
              </div>
            ) : (
              <>
                {/* Invite info chip */}
                <div style={{ marginBottom: 20, padding: "10px 14px", borderRadius: 9, background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.18)", fontSize: 12.5, color: "#3730a3" }}>
                  <span>📧 {inviteInfo.email}</span>
                  <span style={{ margin: "0 8px", color: "rgba(99,102,241,0.3)" }}>·</span>
                  <span style={{ fontWeight: 700 }}>{ROLE_LABEL[inviteInfo.role] || inviteInfo.role}</span>
                </div>

                {error && (
                  <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}
                    style={{ padding: "10px 14px", borderRadius: 10, marginBottom: 14, background: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.35)", fontSize: 12.5, color: "#fca5a5", display: "flex", gap: 8 }}>
                    <span>⚠️</span> {error}
                  </motion.div>
                )}

                <form onSubmit={handleSubmit}>
                  {/* Username */}
                  <div style={{ marginBottom: 14 }}>
                    <label style={labelStyle}>{t("invite.username.label")}</label>
                    <div style={{ position: "relative" }}>
                      <User size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "rgba(99,102,241,0.5)" }} />
                      <input type="text" value={username} onChange={e => setUsername(e.target.value)}
                        placeholder="prenom.nom" required minLength={3} autoFocus
                        style={inputStyle} onFocus={focusStyle} onBlur={blurStyle}
                      />
                    </div>
                  </div>

                  {/* Password */}
                  <div style={{ marginBottom: 14 }}>
                    <label style={labelStyle}>{t("invite.password.label")}</label>
                    <div style={{ position: "relative" }}>
                      <Lock size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "rgba(99,102,241,0.5)" }} />
                      <input type={showPw ? "text" : "password"} value={password}
                        onChange={e => setPassword(e.target.value)}
                        placeholder="••••••••" required minLength={8}
                        style={{ ...inputStyle, paddingRight: 38 }}
                        onFocus={focusStyle} onBlur={blurStyle}
                      />
                      <button type="button" onClick={() => setShowPw(v => !v)} style={{ position: "absolute", right: 11, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", cursor: "pointer", color: "rgba(99,102,241,0.5)", display: "flex", padding: 2 }}>
                        {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  </div>

                  {/* Confirm password */}
                  <div style={{ marginBottom: 22 }}>
                    <label style={labelStyle}>{t("invite.confirm.label")}</label>
                    <div style={{ position: "relative" }}>
                      <Lock size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "rgba(99,102,241,0.5)" }} />
                      <input type={showPw ? "text" : "password"} value={confirm}
                        onChange={e => setConfirm(e.target.value)}
                        placeholder="••••••••" required minLength={8}
                        style={{ ...inputStyle, borderColor: confirm && confirm !== password ? "rgba(239,68,68,0.6)" : undefined }}
                        onFocus={focusStyle} onBlur={blurStyle}
                      />
                    </div>
                  </div>

                  <button type="submit" disabled={loading} style={{
                    width: "100%", padding: "12px",
                    background: loading ? "rgba(99,102,241,0.5)" : "linear-gradient(135deg, #4f46e5 0%, #0284c7 100%)",
                    border: "none", borderRadius: 11, cursor: loading ? "not-allowed" : "pointer",
                    color: "#fff", fontSize: 14, fontWeight: 700,
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                    boxShadow: loading ? "none" : "0 4px 20px rgba(79,70,229,0.45)",
                    transition: "all 150ms",
                  }}>
                    {loading
                      ? <Loader2 size={16} style={{ animation: "spin 1s linear infinite" }} />
                      : <><span>{t("invite.submit")}</span><ArrowRight size={15} /></>
                    }
                  </button>
                </form>
              </>
            )}
          </div>
        </div>

        <style>{`
          input::placeholder { color: rgba(30,58,138,0.28) !important; }
          input:-webkit-autofill { -webkit-box-shadow: 0 0 0 1000px rgba(255,255,255,0.95) inset !important; -webkit-text-fill-color: #1e3a5f !important; }
          @keyframes spin { to { transform: rotate(360deg); } }
        `}</style>
      </motion.div>
    </div>
  );
}
