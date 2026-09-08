import { useState } from "react";
import { useI18n } from "../../context/I18nContext";

const YEAR = new Date().getFullYear();

const CONTENT = {
  privacy: {
    fr: {
      title: "Politique de confidentialité",
      body: `CloudShift — Talan Tunisie collecte uniquement les données nécessaires au fonctionnement de la plateforme (email, identifiants GitHub, configurations cloud).

Ces données sont stockées de manière sécurisée dans une base PostgreSQL hébergée localement et ne sont jamais transmises à des tiers sans consentement explicite.

Vous pouvez demander la suppression de vos données à tout moment en contactant votre administrateur système.

Dernière mise à jour : Juin 2026.`,
    },
    en: {
      title: "Privacy Policy",
      body: `CloudShift — Talan Tunisie only collects data necessary for the platform to function (email, GitHub credentials, cloud configurations).

This data is stored securely in a locally hosted PostgreSQL database and is never shared with third parties without explicit consent.

You may request deletion of your data at any time by contacting your system administrator.

Last updated: June 2026.`,
    },
  },
  terms: {
    fr: {
      title: "Conditions d'utilisation",
      body: `En utilisant CloudShift, vous acceptez les conditions suivantes :

1. Usage interne uniquement — CloudShift est un outil interne développé par Talan Tunisie. Toute utilisation externe est interdite sans autorisation.

2. Responsabilité — L'utilisateur est responsable des migrations lancées depuis son compte. Les déploiements cloud génèrent des coûts facturés au projet concerné.

3. Confidentialité des accès — Les tokens GitHub et clés cloud sont confidentiels. Ne partagez jamais vos identifiants.

4. Disponibilité — La plateforme est fournie sans garantie de disponibilité continue. Des maintenances peuvent interrompre le service.

Dernière mise à jour : Juin 2026.`,
    },
    en: {
      title: "Terms of Use",
      body: `By using CloudShift, you agree to the following terms:

1. Internal use only — CloudShift is an internal tool developed by Talan Tunisie. Any external use is prohibited without authorization.

2. Liability — The user is responsible for migrations launched from their account. Cloud deployments generate costs billed to the relevant project.

3. Access confidentiality — GitHub tokens and cloud keys are confidential. Never share your credentials.

4. Availability — The platform is provided without guarantee of continuous availability. Maintenance may interrupt the service.

Last updated: June 2026.`,
    },
  },
};

function PolicyModal({ type, lang, onClose }) {
  const content = CONTENT[type]?.[lang] || CONTENT[type]?.fr;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 200,
        background: "rgba(15,23,42,0.45)", backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: "#fff", borderRadius: 16, maxWidth: 540, width: "100%",
          boxShadow: "0 24px 64px rgba(0,0,0,0.18)",
          overflow: "hidden",
        }}
      >
        <div style={{
          padding: "18px 24px 14px",
          borderBottom: "1px solid #e2e8f0",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <span style={{ fontWeight: 700, fontSize: 15, color: "#1e3a5f" }}>{content.title}</span>
          <button onClick={onClose} style={{
            background: "none", border: "none", cursor: "pointer",
            fontSize: 20, color: "#94a3b8", lineHeight: 1,
          }}>×</button>
        </div>
        <div style={{
          padding: "20px 24px 24px",
          fontSize: 13, color: "#475569", lineHeight: 1.75,
          whiteSpace: "pre-line", maxHeight: "60vh", overflowY: "auto",
        }}>
          {content.body}
        </div>
      </div>
    </div>
  );
}

export default function AuthFooter({ onLangChange }) {
  const { t, lang, setLang } = useI18n();
  const [modal, setModal] = useState(null);

  const handleLang = (code) => {
    setLang(code);
    onLangChange?.(code);
  };

  const pillBtn = (active) => ({
    padding: "4px 12px", borderRadius: 20, fontSize: 11.5,
    cursor: "pointer", border: "1px solid",
    background: active ? "rgba(99,102,241,0.15)" : "rgba(255,255,255,0.30)",
    color: active ? "#4f46e5" : "rgba(30,58,138,0.55)",
    borderColor: active ? "rgba(99,102,241,0.35)" : "rgba(139,92,246,0.18)",
    fontWeight: active ? 700 : 500,
    transition: "all 140ms",
    fontFamily: "inherit",
    backdropFilter: "blur(4px)",
  });

  return (
    <>
      {modal && <PolicyModal type={modal} lang={lang} onClose={() => setModal(null)} />}
      <footer style={{
        position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 20,
        padding: "10px 28px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", gap: 8,
        background: "linear-gradient(135deg, rgba(219,234,254,0.45) 0%, rgba(224,231,255,0.45) 50%, rgba(237,233,254,0.45) 100%)",
        backdropFilter: "blur(18px)",
        borderTop: "1px solid rgba(139,92,246,0.12)",
        fontSize: 11.5,
      }}>
        {/* Left — copyright + policy buttons */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ color: "rgba(30,58,138,0.45)", fontWeight: 500 }}>
            © {YEAR} CloudShift — Talan Tunisie
          </span>
          <button
            style={pillBtn(false)}
            onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; e.currentTarget.style.color = "#4f46e5"; e.currentTarget.style.borderColor = "rgba(99,102,241,0.35)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "rgba(255,255,255,0.30)"; e.currentTarget.style.color = "rgba(30,58,138,0.55)"; e.currentTarget.style.borderColor = "rgba(139,92,246,0.18)"; }}
            onClick={() => setModal("privacy")}
          >
            {t("footer.privacy")}
          </button>
          <button
            style={pillBtn(false)}
            onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.15)"; e.currentTarget.style.color = "#4f46e5"; e.currentTarget.style.borderColor = "rgba(99,102,241,0.35)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "rgba(255,255,255,0.30)"; e.currentTarget.style.color = "rgba(30,58,138,0.55)"; e.currentTarget.style.borderColor = "rgba(139,92,246,0.18)"; }}
            onClick={() => setModal("terms")}
          >
            {t("footer.terms")}
          </button>
        </div>

        {/* Right — language picker */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ color: "rgba(30,58,138,0.35)", fontSize: 11 }}>{t("footer.lang")}</span>
          {[
            { code: "fr", flag: "🇫🇷", label: "Français" },
            { code: "en", flag: "🇬🇧", label: "English"  },
          ].map(({ code, flag, label }) => (
            <button key={code} onClick={() => handleLang(code)} style={pillBtn(lang === code)}>
              {flag} {label}
            </button>
          ))}
        </div>
      </footer>
    </>
  );
}
