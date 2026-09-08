import { useState } from "react";
import { motion } from "framer-motion";
import {
  Settings2, Key, Cloud, Bell, Palette, Info, Save, Eye, EyeOff,
  CheckCircle, Cpu, Database, Zap, Shield, GitBranch, Package,
  RefreshCw, ExternalLink, AlertTriangle, ChevronRight,
} from "lucide-react";
import { useTheme } from "../hooks/useTheme";
import { useToast } from "../context/ToastContext";
import { useI18n } from "../context/I18nContext";
import { healthCheck } from "../api/migrationApi";

const SECTION_ICONS = {
  llm:    Cpu,
  github: GitBranch,
  clouds: Cloud,
  notifs: Bell,
  appear: Palette,
  about:  Info,
};

function getTechStack(t) {
  return [
    { label: t("settings.stack.llm"),         value: "Azure OpenAI GPT-4o",      color: "var(--azure-color)" },
    { label: t("settings.stack.orchestration"),value: "LangGraph (11 nodes)",      color: "var(--ai-purple)" },
    { label: t("settings.stack.framework"),    value: "FastAPI + SSE",             color: "var(--success-solid)" },
    { label: t("settings.stack.database"),     value: "PostgreSQL 16 + pgvector",  color: "var(--info-solid)" },
    { label: "RAG",                            value: "Graph RAG (pgvector)",       color: "var(--ai-cyan)" },
    { label: t("settings.stack.iac"),          value: "MCP IaC :8001 (checkov)",   color: "var(--warning-solid)" },
    { label: t("settings.stack.templates"),    value: "Jinja2 (deploy.sh + CI/CD)",color: "var(--cs-orange)" },
    { label: t("settings.stack.frontend"),     value: "React 18 + Vite (SSE)",     color: "var(--brand-500)" },
  ];
}

function getCapabilities(t) {
  return [
    { label: t("settings.cap.multicloud"),  desc: "AWS ↔ Azure ↔ GCP",                                                           icon: "✅" },
    { label: t("settings.cap.ai"),          desc: "LLMs · LangGraph · VectorDB · GPU",                                            icon: "✅" },
    { label: t("settings.cap.7r"),          desc: "Retire · Retain · Rehost · Replatform · Repurchase · Refactor · Re-architect", icon: "✅" },
    { label: t("settings.cap.ahp"),         desc: "Analytic Hierarchy Process",                                                   icon: "✅" },
    { label: t("settings.cap.rag"),         desc: "pgvector + Neo4j multi-hop",                                                   icon: "✅" },
    { label: t("settings.cap.standards"),   desc: "MCP + A2A protocols",                                                          icon: "✅" },
    { label: t("settings.cap.gdpr"),        desc: t("settings.cap.gdpr.desc"),                                                    icon: "✅" },
    { label: t("settings.cap.iac"),         desc: "Terraform HCL + Bicep",                                                        icon: "✅" },
    { label: t("settings.cap.security"),    desc: "Checkov · CRITICAL/HIGH",                                                      icon: "✅" },
    { label: t("settings.cap.self"),        desc: t("settings.cap.self.desc"),                                                    icon: "✅" },
  ];
}

function MaskedInput({ value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div style={{ position: "relative" }}>
      <input
        type={show ? "text" : "password"}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="form-input"
        style={{ paddingRight: 38 }}
      />
      <button
        type="button"
        onClick={() => setShow(v => !v)}
        style={{
          position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
          background: "none", border: "none", cursor: "pointer",
          color: "var(--text-tertiary)", display: "flex", alignItems: "center",
        }}
      >
        {show ? <EyeOff size={14} /> : <Eye size={14} />}
      </button>
    </div>
  );
}

function SectionCard({ id, icon: Icon, title, subtitle, children, active, onClick }) {
  return (
    <div style={{
      border: `1px solid ${active ? "var(--brand-500)" : "var(--surface-4)"}`,
      borderRadius: 12,
      overflow: "hidden",
      background: "var(--surface-0)",
      boxShadow: active ? "0 0 0 3px var(--cs-glow)" : "var(--shadow-sm)",
      transition: "all 200ms ease",
    }}>
      <button
        onClick={onClick}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 12,
          padding: "14px 18px",
          background: active ? "var(--brand-50, #F0F9FF)" : "var(--surface-0)",
          border: "none", cursor: "pointer", textAlign: "left",
          borderBottom: active ? "1px solid var(--surface-4)" : "none",
          transition: "background 150ms",
        }}
      >
        <div style={{
          width: 34, height: 34, borderRadius: 9,
          background: active ? "var(--brand-500)" : "var(--surface-3)",
          display: "flex", alignItems: "center", justifyContent: "center",
          flexShrink: 0, transition: "background 200ms",
        }}>
          <Icon size={16} style={{ color: active ? "#fff" : "var(--text-tertiary)" }} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 13.5, color: "var(--text-primary)" }}>{title}</div>
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 1 }}>{subtitle}</div>
        </div>
        <ChevronRight
          size={15}
          style={{
            color: "var(--text-tertiary)",
            transform: active ? "rotate(90deg)" : "none",
            transition: "transform 200ms ease",
          }}
        />
      </button>
      {active && (
        <motion.div
          initial={{ opacity: 0, y: -6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18 }}
          style={{ padding: "18px 20px" }}
        >
          {children}
        </motion.div>
      )}
    </div>
  );
}

export default function Settings() {
  const { theme, toggle } = useTheme();
  const toast = useToast();
  const { t, lang, setLang } = useI18n();
  const [active, setActive] = useState("llm");

  /* LLM state */
  const [llm, setLlm] = useState({
    endpoint:   localStorage.getItem("cs_llm_endpoint") || "",
    apiKey:     "",
    model:      localStorage.getItem("cs_llm_model")    || "gpt-4o",
    apiVersion: localStorage.getItem("cs_llm_version")  || "2025-01-01-preview",
  });

  /* GitHub state */
  const [github, setGithub] = useState({
    token: "",
    defaultOrg: localStorage.getItem("cs_github_org") || "",
  });

  /* Notifications state */
  const [notifs, setNotifs] = useState({
    onComplete: true,
    onFail:     true,
    onHuman:    true,
    onSecurity: true,
  });

  const [apiStatus, setApiStatus] = useState(null);
  const [checking, setChecking] = useState(false);

  const checkApi = async () => {
    setChecking(true);
    try {
      await healthCheck();
      setApiStatus("ok");
      toast.success(t("settings.llm.ok"), { title: t("common.success") });
    } catch {
      setApiStatus("error");
      toast.error(t("settings.llm.fail"), { title: t("common.error") });
    } finally {
      setChecking(false);
    }
  };

  const saveLlm = () => {
    localStorage.setItem("cs_llm_endpoint", llm.endpoint);
    localStorage.setItem("cs_llm_model",    llm.model);
    localStorage.setItem("cs_llm_version",  llm.apiVersion);
    toast.success(t("settings.saved"), { title: t("settings.save") });
  };

  const saveGithub = () => {
    localStorage.setItem("cs_github_org", github.defaultOrg);
    toast.success(t("settings.saved"), { title: t("settings.save") });
  };

  const sections = [
    {
      id: "llm", icon: Cpu,
      title: t("settings.llm.title"),
      subtitle: t("settings.llm.sub"),
      content: (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="form-group">
            <label className="form-label">{t("settings.llm.endpoint")}</label>
            <input
              className="form-input"
              value={llm.endpoint}
              onChange={e => setLlm(p => ({ ...p, endpoint: e.target.value }))}
              placeholder="https://your-resource.openai.azure.com"
            />
          </div>
          <div className="form-group">
            <label className="form-label">{t("settings.llm.key")}</label>
            <MaskedInput
              value={llm.apiKey}
              onChange={e => setLlm(p => ({ ...p, apiKey: e.target.value }))}
              placeholder="••••••••••••••••••••••"
            />
          </div>
          <div className="form-grid">
            <div className="form-group">
              <label className="form-label">{t("settings.llm.model")}</label>
              <select
                className="form-select"
                value={llm.model}
                onChange={e => setLlm(p => ({ ...p, model: e.target.value }))}
              >
                <option value="gpt-4o">gpt-4o</option>
                <option value="gpt-4o-mini">gpt-4o-mini</option>
                <option value="gpt-4-turbo">gpt-4-turbo</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">API Version</label>
              <input
                className="form-input"
                value={llm.apiVersion}
                onChange={e => setLlm(p => ({ ...p, apiVersion: e.target.value }))}
                placeholder="2025-01-01-preview"
              />
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn btn-primary" onClick={saveLlm}>
              <Save size={13} /> {t("settings.save")}
            </button>
          </div>
        </div>
      ),
    },
    {
      id: "github", icon: GitBranch,
      title: t("settings.github.title"),
      subtitle: t("settings.github.sub"),
      content: (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="form-group">
            <label className="form-label">{t("settings.github.token")}</label>
            <MaskedInput
              value={github.token}
              onChange={e => setGithub(p => ({ ...p, token: e.target.value }))}
              placeholder="ghp_••••••••••••••••••••••••"
            />
            <span className="form-hint">Requires repo · read:user · read:org scopes</span>
          </div>
          <div className="form-group">
            <label className="form-label">Organisation par défaut</label>
            <input
              className="form-input"
              value={github.defaultOrg}
              onChange={e => setGithub(p => ({ ...p, defaultOrg: e.target.value }))}
              placeholder="votre-org"
            />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button className="btn btn-primary" onClick={saveGithub}>
              <Save size={13} /> {t("settings.save")}
            </button>
          </div>
        </div>
      ),
    },
    {
      id: "clouds", icon: Cloud,
      title: t("settings.clouds.title"),
      subtitle: t("settings.clouds.sub"),
      content: (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {[
            { id: "aws",   name: "AWS",   note: "Access Key ID + Secret + Region" },
            { id: "azure", name: "Azure", note: "Tenant ID + Client ID + Secret + Subscription" },
            { id: "gcp",   name: "GCP",   note: "Service Account JSON key" },
          ].map(p => (
            <div key={p.id} style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "12px 14px", borderRadius: 9,
              border: "1px solid var(--surface-4)", background: "var(--surface-1)",
            }}>
              <span className={`cloud-label-inline ${p.id}`} style={{ fontSize: 11, padding: "3px 8px" }}>
                {p.name}
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)" }}>{p.name}</div>
                <div style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginTop: 1 }}>{p.note}</div>
              </div>
              <span style={{
                padding: "3px 9px", borderRadius: 20, fontSize: 10.5, fontWeight: 600,
                background: "var(--success-subtle)", color: "var(--success-text)",
                border: "1px solid var(--success-border)",
              }}>
                {lang === "fr" ? "Configuré par migration" : "Configured per migration"}
              </span>
            </div>
          ))}
          <div style={{
            padding: "10px 13px", borderRadius: 8,
            background: "var(--warning-subtle)", border: "1px solid var(--warning-border)",
            fontSize: 12, color: "var(--warning-text)",
          }}>
            <AlertTriangle size={12} style={{ marginRight: 5, verticalAlign: "middle" }} />
            {lang === "fr"
              ? "Les identifiants cloud sont saisis lors de la création de migration et stockés chiffrés. Ils ne sont jamais persistés globalement dans le navigateur."
              : "Cloud credentials are entered during migration creation and stored encrypted per migration. They are never persisted globally in the browser."}
          </div>
        </div>
      ),
    },
    {
      id: "notifs", icon: Bell,
      title: t("settings.notifs.title"),
      subtitle: t("settings.notifs.sub"),
      content: (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {[
            { key: "onComplete", labelKey: "event.Completed",   descFr: "Quand le pipeline se termine avec succès",              descEn: "When a full pipeline finishes successfully" },
            { key: "onFail",     labelKey: "event.Failed",       descFr: "Quand un agent échoue ou dépasse le délai",             descEn: "When any agent step fails or times out" },
            { key: "onHuman",    labelKey: "event.Reviewing",    descFr: "Quand l'IA demande une validation humaine du plan",     descEn: "When the AI requests plan validation" },
            { key: "onSecurity", labelKey: "event.Validating_Intent", descFr: "Quand Checkov détecte des vulnérabilités CRITICAL", descEn: "When checkov detects CRITICAL vulnerabilities" },
          ].map(item => (
            <label key={item.key} style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "10px 13px", borderRadius: 9, cursor: "pointer",
              border: "1px solid var(--surface-4)", background: "var(--surface-1)",
            }}>
              <input
                type="checkbox"
                checked={notifs[item.key]}
                onChange={e => setNotifs(p => ({ ...p, [item.key]: e.target.checked }))}
                style={{ accentColor: "var(--brand-500)", width: 15, height: 15 }}
              />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{t(item.labelKey)}</div>
                <div style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginTop: 1 }}>
                  {lang === "fr" ? item.descFr : item.descEn}
                </div>
              </div>
            </label>
          ))}
        </div>
      ),
    },
    {
      id: "appear", icon: Palette, title: t("settings.appear.title"),
      subtitle: t("settings.appear.sub"),
      content: (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

          {/* ── Theme selector ── */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>{t("settings.theme")}</div>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>
                {t("settings.theme.current")} : <strong>{theme === "dark" ? t("settings.theme.dark") : t("settings.theme.light")}</strong>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              {[["light", `☀️ ${t("settings.theme.light")}`],["dark", `🌙 ${t("settings.theme.dark")}`]].map(([th, label]) => (
                <button
                  key={th}
                  onClick={() => { if (theme !== th) toggle(); }}
                  style={{
                    padding: "7px 16px", borderRadius: 8, fontSize: 12.5, fontWeight: 600,
                    cursor: "pointer", border: "1px solid",
                    background: theme === th ? "var(--brand-500)" : "var(--surface-0)",
                    color:      theme === th ? "#fff"             : "var(--text-secondary)",
                    borderColor: theme === th ? "var(--brand-500)" : "var(--surface-4)",
                    transition: "all 150ms",
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* ── Language selector ── */}
          <div style={{ borderTop: "1px solid var(--surface-3)", paddingTop: 18 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <div>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>{t("settings.language")}</div>
                <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>
                  {t("settings.language.sub")}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                {[
                  { code: "en", label: "🇬🇧 English" },
                  { code: "fr", label: "🇫🇷 Français" },
                ].map(({ code, label }) => (
                  <button
                    key={code}
                    onClick={() => {
                      setLang(code);
                      toast.success(
                        code === "fr" ? "Langue changée en Français." : "Language changed to English.",
                        { title: code === "fr" ? "Langue" : "Language" }
                      );
                    }}
                    style={{
                      padding: "7px 16px", borderRadius: 8, fontSize: 12.5, fontWeight: 600,
                      cursor: "pointer", border: "1px solid",
                      background: lang === code ? "var(--brand-500)" : "var(--surface-0)",
                      color:      lang === code ? "#fff"             : "var(--text-secondary)",
                      borderColor: lang === code ? "var(--brand-500)" : "var(--surface-4)",
                      transition: "all 150ms",
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div style={{
            padding: "10px 13px", borderRadius: 9,
            border: "1px solid var(--surface-4)", background: "var(--surface-1)",
            fontSize: 12.5, color: "var(--text-tertiary)",
          }}>
            {lang === "fr"
              ? "Les préférences sont sauvegardées localement et appliquées immédiatement."
              : "Preferences are saved locally and applied immediately."
            }
          </div>
        </div>
      ),
    },
    {
      id: "about", icon: Info,
      title: t("settings.about.title"),
      subtitle: t("settings.about.sub"),
      content: (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {/* Version + API health */}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {[
              { label: "Platform",  value: "CloudShift v4.0" },
              { label: "Frontend",  value: "React 18 + Vite" },
              { label: "Backend",   value: "FastAPI + LangGraph" },
            ].map(item => (
              <div key={item.label} style={{
                padding: "8px 14px", borderRadius: 8, flex: "1 1 120px",
                background: "var(--surface-2)", border: "1px solid var(--surface-4)",
                textAlign: "center",
              }}>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{item.label}</div>
                <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)", marginTop: 3 }}>{item.value}</div>
              </div>
            ))}
            <div style={{
              padding: "8px 14px", borderRadius: 8, flex: "1 1 120px",
              background: apiStatus === "ok" ? "var(--success-subtle)" : apiStatus === "error" ? "var(--error-subtle)" : "var(--surface-2)",
              border: `1px solid ${apiStatus === "ok" ? "var(--success-border)" : apiStatus === "error" ? "var(--error-border)" : "var(--surface-4)"}`,
              textAlign: "center", cursor: "pointer",
            }}
            onClick={checkApi}
            >
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
                {t("settings.about.apiStatus")}
              </div>
              <div style={{
                fontWeight: 700, fontSize: 13, marginTop: 3,
                color: apiStatus === "ok" ? "var(--success-text)" : apiStatus === "error" ? "var(--error-text)" : "var(--text-primary)",
              }}>
                {checking
                  ? <RefreshCw size={12} style={{ animation: "spin 1s linear infinite" }} />
                  : apiStatus === "ok"
                    ? `✅ ${t("settings.about.healthy")}`
                    : apiStatus === "error"
                      ? `❌ ${t("settings.about.offline")}`
                      : t("settings.about.checkApi")}
              </div>
            </div>
          </div>

          {/* Tech stack */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--text-tertiary)", marginBottom: 8 }}>
              {t("settings.stack.title")}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 6 }}>
              {getTechStack(t).map(s => (
                <div key={s.label} style={{
                  display: "flex", alignItems: "center", gap: 8,
                  padding: "7px 11px", borderRadius: 7,
                  background: "var(--surface-1)", border: "1px solid var(--surface-4)",
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%", background: s.color, flexShrink: 0 }} />
                  <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 80, flexShrink: 0 }}>{s.label}</span>
                  <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-primary)" }}>{s.value}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Capabilities vs competitors */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--text-tertiary)", marginBottom: 8 }}>
              {t("settings.cap.title")}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {getCapabilities(t).map(c => (
                <div key={c.label} style={{
                  display: "flex", alignItems: "flex-start", gap: 8,
                  padding: "7px 11px", borderRadius: 7,
                  background: "var(--success-subtle)", border: "1px solid var(--success-border)",
                }}>
                  <span style={{ fontSize: 13 }}>{c.icon}</span>
                  <div>
                    <span style={{ fontWeight: 600, fontSize: 12.5, color: "var(--text-primary)" }}>{c.label}</span>
                    <span style={{ fontSize: 11.5, color: "var(--text-tertiary)", marginLeft: 8 }}>{c.desc}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ),
    },
  ];

  return (
    <motion.div
      className="page"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
    >
      <div className="page-header">
        <div>
          <h2 className="page-title">{t("settings.title")}</h2>
          <p className="page-subtitle">{t("settings.subtitle")}</p>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 760 }}>
        {sections.map(s => (
          <SectionCard
            key={s.id}
            id={s.id}
            icon={s.icon}
            title={s.title}
            subtitle={s.subtitle}
            active={active === s.id}
            onClick={() => setActive(active === s.id ? null : s.id)}
          >
            {s.content}
          </SectionCard>
        ))}
      </div>
    </motion.div>
  );
}
