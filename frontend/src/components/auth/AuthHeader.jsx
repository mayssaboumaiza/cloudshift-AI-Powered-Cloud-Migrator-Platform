import { useI18n } from "../../context/I18nContext";

function CSLogo({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none">
      <defs>
        <linearGradient id="ah1" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="#4f46e5" />
          <stop offset="100%" stopColor="#0284c7" />
        </linearGradient>
        <linearGradient id="ah2" x1="0" y1="0" x2="32" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="#4f46e5" />
          <stop offset="100%" stopColor="#0284c7" />
        </linearGradient>
      </defs>
      <path d="M10 7C7.2 7 5 9.2 5 12c0 .4 0 .7.1 1C3.9 13.5 3 14.6 3 16c0 1.9 1.6 3.5 3.5 3.5H13V7.3C12.1 7.1 11 7 10 7Z"
        fill="url(#ah1)" opacity="0.4" />
      <path d="M22 7c-1 0-2 .1-2.9.4V19.5h6.4C27.4 19.5 29 17.9 29 16c0-1.4-.9-2.5-2.1-3 .1-.3.1-.6.1-1 0-2.8-2.2-5-5-5Z"
        fill="url(#ah1)" opacity="0.95" />
      <path d="M13 13.5h6M16 11l3 2.5-3 2.5"
        stroke="url(#ah2)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function AuthHeader() {
  const { t } = useI18n();

  return (
    <header style={{
      position: "fixed", top: 0, left: 0, right: 0, zIndex: 20,
      padding: "0 28px",
      height: 54,
      display: "flex", alignItems: "center", justifyContent: "space-between",
      background: "linear-gradient(135deg, rgba(219,234,254,0.45) 0%, rgba(224,231,255,0.45) 50%, rgba(237,233,254,0.45) 100%)",
      backdropFilter: "blur(18px)",
      borderBottom: "1px solid rgba(139,92,246,0.12)",
    }}>
      {/* Logo + nom */}
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <div style={{
          width: 32, height: 32, borderRadius: 9,
          background: "linear-gradient(135deg, rgba(79,70,229,0.18), rgba(2,132,199,0.18))",
          border: "1px solid rgba(139,92,246,0.20)",
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>
          <CSLogo size={18} />
        </div>
        <div>
          <div style={{
            fontSize: 14, fontWeight: 800, letterSpacing: "-0.03em",
            background: "linear-gradient(135deg, #3730a3, #0369a1)",
            WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent", backgroundClip: "text",
            lineHeight: 1,
          }}>CloudShift</div>
          <div style={{ fontSize: 9, color: "rgba(30,58,138,0.45)", letterSpacing: "0.07em",
            textTransform: "uppercase", fontWeight: 600, marginTop: 1 }}>
            {t("auth.subtitle")}
          </div>
        </div>
      </div>

      {/* API status pill */}
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "5px 12px", borderRadius: 20,
        background: "rgba(34,197,94,0.10)",
        border: "1px solid rgba(34,197,94,0.25)",
      }}>
        <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e",
          boxShadow: "0 0 6px #22c55e", display: "inline-block", flexShrink: 0 }} />
        <span style={{ fontSize: 10.5, color: "#16a34a", fontWeight: 600, letterSpacing: "0.03em" }}>
          API OK
        </span>
      </div>
    </header>
  );
}
