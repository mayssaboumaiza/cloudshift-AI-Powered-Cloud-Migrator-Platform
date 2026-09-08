import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { Home, ArrowLeft } from "lucide-react";
import { useI18n } from "../context/I18nContext";
import AuthBackground from "../components/auth/AuthBackground";
import AuthHeader from "../components/auth/AuthHeader";
import AuthFooter from "../components/auth/AuthFooter";

export default function NotFound() {
  const { t } = useI18n();
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
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        style={{ position: "relative", zIndex: 10, textAlign: "center", padding: "0 24px" }}
      >
        <div style={{
          fontSize: 96, fontWeight: 900, letterSpacing: "-0.06em",
          background: "linear-gradient(135deg, #3730a3 0%, #1d4ed8 50%, #0369a1 100%)",
          WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", backgroundClip: "text",
          lineHeight: 1,
        }}>404</div>
        <div style={{ fontSize: 22, fontWeight: 700, color: "#1e3a5f", marginTop: 12 }}>
          {t("notfound.title")}
        </div>
        <div style={{ fontSize: 14, color: "rgba(30,58,138,0.5)", marginTop: 8, maxWidth: 340, margin: "8px auto 32px" }}>
          {t("notfound.desc")}
        </div>
        <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
          <Link to="/dashboard" style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "11px 22px", borderRadius: 11,
            background: "linear-gradient(135deg, #4f46e5 0%, #0284c7 100%)",
            color: "#fff", fontWeight: 700, fontSize: 14, textDecoration: "none",
            boxShadow: "0 4px 20px rgba(79,70,229,0.45)",
          }}>
            <Home size={15} /> {t("notfound.home")}
          </Link>
          <button onClick={() => window.history.back()} style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "11px 22px", borderRadius: 11,
            background: "rgba(255,255,255,0.8)", border: "1px solid rgba(99,102,241,0.25)",
            color: "#3730a3", fontWeight: 600, fontSize: 14, cursor: "pointer",
          }}>
            <ArrowLeft size={15} /> {t("notfound.back")}
          </button>
        </div>
      </motion.div>
    </div>
  );
}
