import { Component } from "react";
import { motion } from "framer-motion";
import { AlertTriangle, RefreshCw, Wifi, WifiOff } from "lucide-react";

const MSGS = {
  fr: {
    net_title:   "Problème de connexion",
    net_sub:     "CloudShift ne peut pas joindre le serveur API.",
    err_title:   "Une erreur est survenue",
    err_sub:     "Une erreur inattendue s'est produite dans l'interface.",
    api_online:  "Serveur API : en ligne",
    api_offline: "Serveur API : inaccessible",
    details:     "Détails techniques",
    net_check:   "Vérifiez que les conteneurs Docker tournent",
    net_cmd1:    "Vérifiez la santé de l'API",
    net_cmd2:    "Consultez les logs",
    err_check:   "Que faire :",
    err_step1:   "Cliquez sur « Réessayer » pour recharger",
    err_step2:   "Si l'erreur persiste, vérifiez la console (F12)",
    err_step3:   "Contactez l'administrateur si le problème continue",
    retry:       "Réessayer",
    checking:    "Vérification…",
    home:        "Accueil",
  },
  en: {
    net_title:   "Connection issue",
    net_sub:     "CloudShift cannot reach the API server.",
    err_title:   "Something went wrong",
    err_sub:     "An unexpected error occurred in the interface.",
    api_online:  "API server: online",
    api_offline: "API server: unreachable",
    details:     "Technical details",
    net_check:   "Make sure Docker containers are running",
    net_cmd1:    "Verify the API is healthy",
    net_cmd2:    "Check container logs",
    err_check:   "What to do:",
    err_step1:   "Click \"Retry\" to reload the page",
    err_step2:   "If the error persists, check the browser console (F12)",
    err_step3:   "Contact the administrator if the problem continues",
    retry:       "Retry",
    checking:    "Checking…",
    home:        "Go Home",
  },
};

/* ── API health probe — shown in the fallback UI ───────────────────────── */
async function checkApi() {
  try {
    const res = await fetch("/health", { signal: AbortSignal.timeout(4000) });
    return res.ok;
  } catch {
    return false;
  }
}

/* ── Error boundary class component (React requires class for error boundaries) */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, apiOnline: true, checking: false };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("[ErrorBoundary]", error, info);
  }

  handleRetry = async () => {
    this.setState({ checking: true });
    const apiOnline = await checkApi();
    this.setState({ checking: false, apiOnline });
    if (apiOnline) {
      // Clear error state to re-render the tree
      this.setState({ hasError: false, error: null });
    }
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    const { error, apiOnline, checking } = this.state;
    const isNetworkError =
      error?.message?.includes("fetch") ||
      error?.message?.includes("NetworkError") ||
      error?.message?.includes("Failed to fetch") ||
      !apiOnline;

    const lang = localStorage.getItem("cs_lang") || "fr";
    const m = MSGS[lang] || MSGS.fr;

    return (
      <div style={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        background: "#f8fafc", fontFamily: "system-ui, sans-serif",
        padding: 24,
      }}>
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          style={{
            maxWidth: 520, width: "100%",
            background: "#fff", borderRadius: 16,
            border: "1px solid #e2e8f0",
            boxShadow: "0 4px 24px rgba(0,0,0,0.07)",
            overflow: "hidden",
          }}
        >
          {/* Header */}
          <div style={{
            padding: "24px 28px 20px",
            background: isNetworkError
              ? "linear-gradient(135deg, #fffbeb, #fef3c7)"
              : "linear-gradient(135deg, #fef2f2, #fee2e2)",
            borderBottom: `1px solid ${isNetworkError ? "#fde68a" : "#fecaca"}`,
            display: "flex", alignItems: "center", gap: 14,
          }}>
            <div style={{
              width: 48, height: 48, borderRadius: 12, flexShrink: 0,
              background: isNetworkError ? "#fbbf24" : "#ef4444",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              {isNetworkError
                ? <WifiOff size={22} color="#fff" />
                : <AlertTriangle size={22} color="#fff" />
              }
            </div>
            <div>
              <div style={{ fontWeight: 800, fontSize: 17, color: "#0f172a" }}>
                {isNetworkError ? m.net_title : m.err_title}
              </div>
              <div style={{ fontSize: 13, color: "#64748b", marginTop: 3 }}>
                {isNetworkError ? m.net_sub : m.err_sub}
              </div>
            </div>
          </div>

          {/* Body */}
          <div style={{ padding: "22px 28px" }}>
            {/* API status indicator */}
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "10px 14px", borderRadius: 9,
              background: apiOnline ? "#f0fdf4" : "#fef2f2",
              border: `1px solid ${apiOnline ? "#bbf7d0" : "#fecaca"}`,
              marginBottom: 18, fontSize: 13,
            }}>
              {apiOnline
                ? <Wifi size={14} style={{ color: "#16a34a", flexShrink: 0 }} />
                : <WifiOff size={14} style={{ color: "#dc2626", flexShrink: 0 }} />
              }
              <span style={{ color: apiOnline ? "#15803d" : "#dc2626", fontWeight: 600 }}>
                {apiOnline ? m.api_online : m.api_offline}
              </span>
              <span style={{ color: "#94a3b8", fontSize: 11.5, marginLeft: "auto" }}>
                http://localhost:8000
              </span>
            </div>

            {/* Error message */}
            {error?.message && (
              <details style={{ marginBottom: 18 }}>
                <summary style={{
                  cursor: "pointer", fontSize: 12.5, fontWeight: 600,
                  color: "#64748b", marginBottom: 6,
                }}>
                  {m.details}
                </summary>
                <pre style={{
                  fontSize: 11.5, background: "#0f172a", color: "#94a3b8",
                  padding: "10px 12px", borderRadius: 7, overflowX: "auto",
                  lineHeight: 1.6, margin: 0,
                }}>
                  {error.message}
                  {error.stack && "\n\n" + error.stack.split("\n").slice(0, 5).join("\n")}
                </pre>
              </details>
            )}

            {/* What to do */}
            <div style={{
              padding: "12px 14px", borderRadius: 9,
              background: "#f8fafc", border: "1px solid #e2e8f0",
              fontSize: 12.5, color: "#475569", lineHeight: 1.7,
              marginBottom: 20,
            }}>
              {isNetworkError ? (
                <>
                  <strong>{m.net_check} :</strong><br />
                  • {m.net_cmd1}: <code style={{ background: "#e2e8f0", padding: "1px 4px", borderRadius: 3 }}>docker compose up</code><br />
                  • {m.net_cmd2}: <code style={{ background: "#e2e8f0", padding: "1px 4px", borderRadius: 3 }}>curl http://localhost:8000/health</code><br />
                  • Logs: <code style={{ background: "#e2e8f0", padding: "1px 4px", borderRadius: 3 }}>docker logs cloud-migrator-api</code>
                </>
              ) : (
                <>
                  <strong>{m.err_check}</strong><br />
                  • {m.err_step1}<br />
                  • {m.err_step2}<br />
                  • {m.err_step3}
                </>
              )}
            </div>

            {/* Actions */}
            <div style={{ display: "flex", gap: 10 }}>
              <button
                onClick={this.handleRetry}
                disabled={checking}
                style={{
                  flex: 1, padding: "10px", borderRadius: 9, border: "none",
                  background: "linear-gradient(135deg, #4f46e5, #0284c7)",
                  color: "#fff", fontWeight: 700, fontSize: 13.5, cursor: "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
                  opacity: checking ? 0.7 : 1,
                }}
              >
                <RefreshCw size={15} style={{ animation: checking ? "spin 1s linear infinite" : "none" }} />
                {checking ? m.checking : m.retry}
              </button>
              <button
                onClick={() => window.location.href = "/"}
                style={{
                  padding: "10px 18px", borderRadius: 9,
                  border: "1px solid #e2e8f0", background: "#fff",
                  color: "#475569", fontWeight: 600, fontSize: 13.5, cursor: "pointer",
                }}
              >
                {m.home}
              </button>
            </div>
          </div>
        </motion.div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }
}
