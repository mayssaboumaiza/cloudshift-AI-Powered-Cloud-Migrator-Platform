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
          background: "var(--surface-0, #fff)", borderRadius: 16, maxWidth: 540, width: "100%",
          boxShadow: "0 24px 64px rgba(0,0,0,0.18)",
          overflow: "hidden",
          border: "1px solid var(--surface-4, #e2e8f0)",
        }}
      >
        <div style={{
          padding: "18px 24px 14px",
          borderBottom: "1px solid var(--surface-4, #e2e8f0)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
        }}>
          <span style={{ fontWeight: 700, fontSize: 15, color: "var(--text-primary, #1e3a5f)" }}>{content.title}</span>
          <button onClick={onClose} style={{
            background: "none", border: "none", cursor: "pointer",
            fontSize: 20, color: "var(--text-disabled, #94a3b8)", lineHeight: 1,
          }}>×</button>
        </div>
        <div style={{
          padding: "20px 24px 24px",
          fontSize: 13, color: "var(--text-secondary, #475569)", lineHeight: 1.75,
          whiteSpace: "pre-line", maxHeight: "60vh", overflowY: "auto",
        }}>
          {content.body}
        </div>
      </div>
    </div>
  );
}

export default function AppFooter() {
  const { t, lang } = useI18n();
  const [modal, setModal] = useState(null);

  const linkStyle = {
    color: "var(--text-disabled)", textDecoration: "none", transition: "color 120ms",
    background: "none", border: "none", cursor: "pointer",
    fontSize: 11.5, padding: 0, fontFamily: "inherit",
  };

  return (
    <>
      {modal && <PolicyModal type={modal} lang={lang} onClose={() => setModal(null)} />}
      <footer style={{
        borderTop: "1px solid var(--surface-4)",
        background: "var(--surface-0)",
        padding: "10px 28px",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexWrap: "wrap", gap: 8,
        fontSize: 11.5,
        flexShrink: 0,
      }}>
        {/* Left — copyright + liens */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, color: "var(--text-disabled)" }}>
          <span>© {YEAR} CloudShift — Talan Tunisie</span>
          <span style={{ opacity: 0.4 }}>·</span>
          <button style={linkStyle}
            onMouseEnter={e => e.currentTarget.style.color = "var(--brand-500)"}
            onMouseLeave={e => e.currentTarget.style.color = "var(--text-disabled)"}
            onClick={() => setModal("privacy")}
          >
            {t("footer.privacy")}
          </button>
          <span style={{ opacity: 0.4 }}>·</span>
          <button style={linkStyle}
            onMouseEnter={e => e.currentTarget.style.color = "var(--brand-500)"}
            onMouseLeave={e => e.currentTarget.style.color = "var(--text-disabled)"}
            onClick={() => setModal("terms")}
          >
            {t("footer.terms")}
          </button>
        </div>

        {/* Right — stack info */}
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-disabled)" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#22c55e",
              boxShadow: "0 0 5px #22c55e", display: "inline-block" }} />
            FastAPI · LangGraph · GPT-4o
          </span>
          <span style={{ opacity: 0.4 }}>·</span>
          <span style={{ color: "var(--text-disabled)", fontSize: 11 }}>
            {lang === "fr" ? "Fabriqué avec ❤ en Tunisie" : "Made with ❤ in Tunisia"}
          </span>
        </div>
      </footer>
    </>
  );
}
