import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Mail, Lock, Eye, EyeOff, User, Loader2, ArrowRight } from "lucide-react";
import { register } from "../api/authApi";
import { useAuth } from "../context/AuthContext";
import { useToast } from "../context/ToastContext";
import AuthBackground from "../components/auth/AuthBackground";
import AuthHeader from "../components/auth/AuthHeader";
import AuthFooter from "../components/auth/AuthFooter";
import { useI18n } from "../context/I18nContext";

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

function CSLogo({ size = 44 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <defs>
        <linearGradient id="rg1" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#4f46e5" />
          <stop offset="100%" stopColor="#0284c7" />
        </linearGradient>
        <linearGradient id="rg2" x1="0" y1="0" x2="32" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#4f46e5" />
          <stop offset="100%" stopColor="#0284c7" />
        </linearGradient>
      </defs>
      <path d="M10 7C7.2 7 5 9.2 5 12c0 .4 0 .7.1 1C3.9 13.5 3 14.6 3 16c0 1.9 1.6 3.5 3.5 3.5H13V7.3C12.1 7.1 11 7 10 7Z" fill="url(#rg1)" opacity="0.4" />
      <path d="M22 7c-1 0-2 .1-2.9.4V19.5h6.4C27.4 19.5 29 17.9 29 16c0-1.4-.9-2.5-2.1-3 .1-.3.1-.6.1-1 0-2.8-2.2-5-5-5Z" fill="url(#rg1)" opacity="0.95" />
      <path d="M13 13.5h6M16 11l3 2.5-3 2.5" stroke="url(#rg2)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="26" cy="8" r="2" fill="#4f46e5" opacity="0.9" />
      <path d="M26 6.3V7m0 2v.7M24.5 8H25m2 0h.5M24.9 6.9l.4.4m1.4 1.4.4.4M27.1 6.9l-.4.4m-1.4 1.4-.4.4" stroke="white" strokeWidth="0.6" strokeLinecap="round" />
    </svg>
  );
}

export default function Register() {
  const navigate = useNavigate();
  const toast    = useToast();
  const { login } = useAuth();
  const { t } = useI18n();

  const [username, setUsername] = useState("");
  const [email,    setEmail]    = useState("");
  const [password, setPassword] = useState("");
  const [confirm,  setConfirm]  = useState("");
  const [showPw,   setShowPw]   = useState(false);
  const [showCf,   setShowCf]   = useState(false);
  const [loading,  setLoading]  = useState(false);
  const [error,    setError]    = useState(null);

  const focusStyle = (e) => {
    e.target.style.borderColor = "rgba(99,102,241,0.75)";
    e.target.style.background  = "#fff";
    e.target.style.boxShadow   = "0 0 0 3px rgba(99,102,241,0.12)";
  };
  const blurStyle = (e) => {
    e.target.style.borderColor = "rgba(99,102,241,0.22)";
    e.target.style.background  = "rgba(255,255,255,0.85)";
    e.target.style.boxShadow   = "none";
  };

  const pwStrength = (pw) => {
    if (!pw) return 0;
    let s = 0;
    if (pw.length >= 8)  s++;
    if (pw.length >= 12) s++;
    if (/[A-Z]/.test(pw)) s++;
    if (/[0-9]/.test(pw)) s++;
    if (/[^A-Za-z0-9]/.test(pw)) s++;
    return s; // 0-5
  };
  const strength = pwStrength(password);
  const strengthColor = ["#e5e7eb","#ef4444","#f97316","#eab308","#22c55e","#16a34a"][strength];
  const strengthLabel = [
    "", t("auth.pwd.weak"), t("auth.pwd.fair"), t("auth.pwd.good"), t("auth.pwd.strong"), t("auth.pwd.vstrong")
  ][strength];

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) { setError(t("auth.pwd.mismatch")); return; }
    if (password.length < 8)  { setError(t("auth.pwd.short")); return; }
    setLoading(true);
    try {
      await register(email, username, password);
      await login(email, password);
      toast.success(t("auth.created.desc"), { title: t("auth.created.title"), duration: 5000 });
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err.message || t("auth.created.error"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "var(--font-ui, system-ui)", position: "relative", overflow: "hidden",
      paddingTop: 54, paddingBottom: 56,
    }}>
      <AuthBackground />
      <AuthHeader />

      <motion.div
        initial={{ opacity: 0, y: 20, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
        style={{ position: "relative", zIndex: 10, width: "100%", maxWidth: 420, padding: "0 16px" }}
      >
        {/* Brand */}
        <div style={{ textAlign: "center", marginBottom: 28 }}>
          <div style={{
            width: 68, height: 68, borderRadius: 18, margin: "0 auto 14px",
            background: "linear-gradient(135deg, rgba(79,70,229,0.18), rgba(2,132,199,0.18))",
            border: "1px solid rgba(255,255,255,0.6)", backdropFilter: "blur(12px)",
            display: "flex", alignItems: "center", justifyContent: "center",
            boxShadow: "0 8px 32px rgba(99,102,241,0.25)",
          }}>
            <CSLogo size={42} />
          </div>
          <div style={{
            fontSize: 28, fontWeight: 900, letterSpacing: "-0.04em",
            background: "linear-gradient(135deg, #3730a3 0%, #1d4ed8 50%, #0369a1 100%)",
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", backgroundClip: "text",
            marginBottom: 6,
          }}>
            CloudShift
          </div>
          <div style={{ fontSize: 12, color: "rgba(30,58,138,0.5)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 600 }}>
            {t("auth.subtitle")}
          </div>
        </div>

        {/* Card */}
        <div style={{
          background: "rgba(255,255,255,0.78)", backdropFilter: "blur(22px)",
          borderRadius: 18, border: "1px solid rgba(255,255,255,0.95)",
          boxShadow: "0 24px 64px rgba(99,102,241,0.14), 0 4px 20px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,1)",
          overflow: "hidden",
        }}>
          <div style={{ padding: "18px 28px 16px", borderBottom: "1px solid rgba(99,102,241,0.08)" }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: "#3730a3" }}>{t("auth.register")}</div>
          </div>

          <div style={{ padding: "26px 28px 24px" }}>
            <AnimatePresence>
              {error && (
                <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                  style={{ padding: "10px 14px", borderRadius: 10, marginBottom: 18, background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", fontSize: 12.5, color: "#dc2626", display: "flex", alignItems: "center", gap: 8 }}>
                  <span>⚠️</span> {error}
                </motion.div>
              )}
            </AnimatePresence>

            <form onSubmit={handleSubmit}>
              {/* Username */}
              <div style={{ marginBottom: 14 }}>
                <label style={labelStyle}>{t("auth.username")}</label>
                <div style={{ position: "relative" }}>
                  <User size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "rgba(99,102,241,0.5)" }} />
                  <input type="text" value={username} onChange={e => setUsername(e.target.value)}
                    placeholder="john.doe" required minLength={3}
                    style={inputStyle} onFocus={focusStyle} onBlur={blurStyle}
                  />
                </div>
              </div>

              {/* Email */}
              <div style={{ marginBottom: 14 }}>
                <label style={labelStyle}>{t("auth.email")}</label>
                <div style={{ position: "relative" }}>
                  <Mail size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "rgba(99,102,241,0.5)" }} />
                  <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                    placeholder="vous@entreprise.com" required
                    style={inputStyle} onFocus={focusStyle} onBlur={blurStyle}
                  />
                </div>
              </div>

              {/* Password + force indicator */}
              <div style={{ marginBottom: 14 }}>
                <label style={labelStyle}>{t("auth.password")}</label>
                <div style={{ position: "relative" }}>
                  <Lock size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "rgba(99,102,241,0.5)" }} />
                  <input type={showPw ? "text" : "password"} value={password}
                    onChange={e => setPassword(e.target.value)}
                    placeholder="••••••••" required minLength={8}
                    style={{ ...inputStyle, paddingRight: 38 }}
                    onFocus={focusStyle} onBlur={blurStyle}
                  />
                  <button type="button" onClick={() => setShowPw(v => !v)} style={{
                    position: "absolute", right: 11, top: "50%", transform: "translateY(-50%)",
                    background: "none", border: "none", cursor: "pointer",
                    color: "rgba(99,102,241,0.5)", display: "flex", alignItems: "center", padding: 2,
                  }}>
                    {showPw ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
                {/* Strength bar */}
                {password.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ display: "flex", gap: 4, marginBottom: 4 }}>
                      {[1,2,3,4,5].map(i => (
                        <div key={i} style={{
                          flex: 1, height: 3, borderRadius: 4,
                          background: i <= strength ? strengthColor : "#e5e7eb",
                          transition: "background 200ms",
                        }} />
                      ))}
                    </div>
                    <div style={{ fontSize: 11, color: strengthColor, fontWeight: 600 }}>{strengthLabel}</div>
                  </div>
                )}
              </div>

              {/* Confirm */}
              <div style={{ marginBottom: 22 }}>
                <label style={labelStyle}>{t("auth.confirm.pwd")}</label>
                <div style={{ position: "relative" }}>
                  <Lock size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "rgba(99,102,241,0.5)" }} />
                  <input type={showCf ? "text" : "password"} value={confirm}
                    onChange={e => setConfirm(e.target.value)}
                    placeholder="••••••••" required minLength={8}
                    style={{ ...inputStyle, paddingRight: 38 }}
                    onFocus={focusStyle} onBlur={blurStyle}
                  />
                  <button type="button" onClick={() => setShowCf(v => !v)} style={{
                    position: "absolute", right: 11, top: "50%", transform: "translateY(-50%)",
                    background: "none", border: "none", cursor: "pointer",
                    color: "rgba(99,102,241,0.5)", display: "flex", alignItems: "center", padding: 2,
                  }}>
                    {showCf ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>

              <button
                type="submit" disabled={loading}
                style={{
                  width: "100%", padding: "12px",
                  background: loading ? "rgba(99,102,241,0.5)" : "linear-gradient(135deg, #4f46e5 0%, #0284c7 100%)",
                  border: "none", borderRadius: 11, cursor: loading ? "not-allowed" : "pointer",
                  color: "#fff", fontSize: 14, fontWeight: 700,
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  boxShadow: loading ? "none" : "0 4px 20px rgba(79,70,229,0.45)",
                  transition: "all 150ms",
                }}
                onMouseEnter={e => { if (!loading) e.currentTarget.style.transform = "translateY(-1px)"; }}
                onMouseLeave={e => { e.currentTarget.style.transform = "none"; }}
              >
                {loading
                  ? <Loader2 size={16} style={{ animation: "spin 1s linear infinite" }} />
                  : <><span>{t("auth.register.btn")}</span><ArrowRight size={15} /></>
                }
              </button>
            </form>

            <div style={{ textAlign: "center", marginTop: 18 }}>
              <Link to="/login"
                style={{ fontSize: 12.5, color: "rgba(30,58,138,0.45)", textDecoration: "none" }}
                onMouseEnter={e => e.currentTarget.style.color = "#4f46e5"}
                onMouseLeave={e => e.currentTarget.style.color = "rgba(30,58,138,0.45)"}
              >
                {t("auth.have.account")} <span style={{ fontWeight: 700 }}>{t("auth.signin.btn")}</span>
              </Link>
            </div>
          </div>
        </div>
      </motion.div>

      <AuthFooter />

      <style>{`
        input::placeholder { color: rgba(30,58,138,0.28) !important; }
        input:-webkit-autofill {
          -webkit-box-shadow: 0 0 0 1000px rgba(255,255,255,0.95) inset !important;
          -webkit-text-fill-color: #1e3a5f !important;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
