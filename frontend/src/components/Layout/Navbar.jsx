import { useLocation, useNavigate, Link } from "react-router-dom";
import { Moon, Sun, Search, ChevronRight, ArrowRight, User, LogOut } from "lucide-react";
import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { healthCheck } from "../../api/migrationApi";
import { useTheme } from "../../hooks/useTheme";
import { useI18n } from "../../context/I18nContext";
import { useAuth } from "../../context/AuthContext";
import AlertsCenter from "../common/AlertsCenter";

function buildCrumbs(path, t) {
  if (path === "/dashboard")           return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.migrations") }];
  if (path === "/migrations/new")      return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.migrations"), to: "/dashboard" }, { label: t("create.title") }];
  if (path.startsWith("/migrations/")) return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.migrations"), to: "/dashboard" }, { label: t("common.details") }];
  if (path === "/settings")            return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.settings") }];
  if (path === "/activity")            return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.activity") }];
  if (path === "/analytics")           return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.analytics") }];
  if (path === "/architecture")        return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.architecture") }];
  if (path === "/assistant")           return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.assistant") }];
  if (path === "/profile")             return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.profile") }];
  if (path === "/admin/users")         return [{ label: "CloudShift", to: "/dashboard" }, { label: t("nav.users") }];
  return [{ label: "CloudShift" }];
}

/* ── Command palette (real search) ──────────────────────────────────────── */
function CommandPalette({ open, onClose, migrations }) {
  const [query, setQuery]       = useState("");
  const [selected, setSelected] = useState(0);
  const navigate                = useNavigate();
  const inputRef                = useRef(null);
  const { t }                   = useI18n();

  const QUICK_ACTIONS = [
    { icon: "✦", label: t("nav.new"),        desc: t("cmd.new.desc"),      to: "/migrations/new", color: "var(--cs-orange)" },
    { icon: "📋", label: t("nav.migrations"), desc: t("cmd.all.desc"),      to: "/dashboard",      color: "var(--brand-500)" },
    { icon: "⚡", label: t("nav.activity"),   desc: t("cmd.activity.desc"), to: "/activity",       color: "var(--ai-purple)"  },
    { icon: "⚙️", label: t("nav.settings"),  desc: t("cmd.settings.desc"), to: "/settings",       color: "var(--text-tertiary)"},
  ];

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelected(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const migResults = !query.trim() ? [] : migrations.filter(m =>
    m.repo_url.toLowerCase().includes(query.toLowerCase()) ||
    m.id.toLowerCase().includes(query.toLowerCase())
  ).slice(0, 5);

  const actions = query.trim()
    ? QUICK_ACTIONS.filter(a =>
        a.label.toLowerCase().includes(query.toLowerCase()) ||
        a.desc.toLowerCase().includes(query.toLowerCase())
      )
    : QUICK_ACTIONS;

  const allItems = [
    ...actions.map(a => ({ type: "action", ...a })),
    ...migResults.map(m => ({
      type: "migration",
      label: m.repo_url.replace("https://github.com/", ""),
      desc:  `${m.source_cloud?.toUpperCase()} → ${m.target_cloud?.toUpperCase()} · ${m.status}`,
      to:    `/migrations/${m.id}`,
      icon:  "🔀",
    })),
  ];

  const go = (item) => {
    navigate(item.to);
    onClose();
  };

  useEffect(() => {
    const handler = (e) => {
      if (!open) return;
      if (e.key === "ArrowDown")  { e.preventDefault(); setSelected(s => Math.min(s + 1, allItems.length - 1)); }
      if (e.key === "ArrowUp")    { e.preventDefault(); setSelected(s => Math.max(s - 1, 0)); }
      if (e.key === "Enter" && allItems[selected]) go(allItems[selected]);
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, allItems, selected]);

  if (!open) return null;

  return (
    <AnimatePresence>
      {open && (
        <>
          {/* Backdrop */}
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            onClick={onClose}
            style={{
              position: "fixed", inset: 0, zIndex: 1000,
              background: "rgba(10, 15, 30, 0.4)",
              backdropFilter: "blur(4px)",
            }}
          />
          {/* Panel */}
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: -12 }}
            animate={{ opacity: 1, scale: 1,    y: 0     }}
            exit={{   opacity: 0, scale: 0.96, y: -8     }}
            transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
            style={{
              position: "fixed", top: "18%", left: "50%",
              transform: "translateX(-50%)",
              width: 540, zIndex: 1001,
              borderRadius: 14,
              background: "var(--surface-0)",
              border: "1px solid var(--surface-5)",
              boxShadow: "0 20px 60px rgba(0,0,0,0.15), 0 4px 16px rgba(0,0,0,0.08)",
              overflow: "hidden",
            }}
          >
            {/* Search input */}
            <div style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "14px 16px",
              borderBottom: "1px solid var(--surface-4)",
            }}>
              <Search size={16} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
              <input
                ref={inputRef}
                value={query}
                onChange={e => { setQuery(e.target.value); setSelected(0); }}
                placeholder={t("cmd.placeholder")}
                style={{
                  flex: 1, border: "none", background: "none", outline: "none",
                  fontSize: 15, color: "var(--text-primary)",
                  fontFamily: "var(--font-ui)",
                }}
              />
              <kbd style={{
                padding: "2px 6px", borderRadius: 5, fontSize: 10,
                fontFamily: "var(--font-mono)",
                background: "var(--surface-3)", border: "1px solid var(--surface-5)",
                color: "var(--text-disabled)",
              }}>Esc</kbd>
            </div>

            {/* Results */}
            <div style={{ maxHeight: 340, overflowY: "auto", padding: "6px 8px" }}>
              {allItems.length === 0 && (
                <div style={{ padding: "20px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>
                  {t("cmd.no_results", { q: query })}
                </div>
              )}

              {!query.trim() && (
                <div style={{
                  fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.09em", color: "var(--text-disabled)",
                  padding: "4px 10px 2px",
                }}>
                  {t("cmd.quick_actions")}
                </div>
              )}

              {actions.map((item, i) => (
                <button
                  key={item.label}
                  onClick={() => go(item)}
                  onMouseEnter={() => setSelected(i)}
                  style={{
                    width: "100%", display: "flex", alignItems: "center", gap: 10,
                    padding: "9px 10px", borderRadius: 8, border: "none", cursor: "pointer",
                    background: selected === i ? "var(--surface-2)" : "transparent",
                    textAlign: "left", transition: "background 80ms",
                  }}
                >
                  <span style={{ fontSize: 16, width: 24, textAlign: "center", flexShrink: 0 }}>{item.icon}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>{item.label}</div>
                    <div style={{ fontSize: 11.5, color: "var(--text-tertiary)" }}>{item.desc}</div>
                  </div>
                  {selected === i && <ArrowRight size={13} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />}
                </button>
              ))}

              {migResults.length > 0 && (
                <div style={{
                  fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.09em", color: "var(--text-disabled)",
                  padding: "8px 10px 2px",
                  borderTop: actions.length ? "1px solid var(--surface-3)" : "none",
                  marginTop: actions.length ? 4 : 0,
                }}>
                  {t("nav.migrations")}
                </div>
              )}

              {migResults.map((item, idx) => {
                const i = actions.length + idx;
                return (
                  <button
                    key={item.id}
                    onClick={() => go({ to: `/migrations/${item.id}` })}
                    onMouseEnter={() => setSelected(i)}
                    style={{
                      width: "100%", display: "flex", alignItems: "center", gap: 10,
                      padding: "9px 10px", borderRadius: 8, border: "none", cursor: "pointer",
                      background: selected === i ? "var(--surface-2)" : "transparent",
                      textAlign: "left", transition: "background 80ms",
                    }}
                  >
                    <span style={{ fontSize: 16, width: 24, textAlign: "center", flexShrink: 0 }}>🔀</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontWeight: 600, fontSize: 13, color: "var(--text-primary)",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>
                        {item.repo_url.replace("https://github.com/", "")}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                        {item.source_cloud?.toUpperCase()} → {item.target_cloud?.toUpperCase()} · {item.status}
                      </div>
                    </div>
                    {selected === i && <ArrowRight size={13} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />}
                  </button>
                );
              })}
            </div>

            {/* Footer */}
            <div style={{
              display: "flex", gap: 12, padding: "8px 16px",
              borderTop: "1px solid var(--surface-4)",
              fontSize: 10.5, color: "var(--text-disabled)",
            }}>
              {[["↑↓", t("cmd.key.nav")], ["↵", t("cmd.key.open")], ["Esc", t("cmd.key.close")]].map(([k, l]) => (
                <span key={k} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <kbd style={{
                    padding: "1px 5px", borderRadius: 4, background: "var(--surface-3)",
                    border: "1px solid var(--surface-5)", fontFamily: "var(--font-mono)", fontSize: 9.5,
                  }}>{k}</kbd>
                  {l}
                </span>
              ))}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

export default function Navbar() {
  const { theme, toggle }   = useTheme();
  const { t }               = useI18n();
  const { user, logout }    = useAuth();
  const loc                 = useLocation();
  const navigate            = useNavigate();
  const [health, setHealth]       = useState(null);
  const [cmdOpen, setCmdOpen]     = useState(false);
  const [migrations, setMigrations] = useState([]);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target)) {
        setUserMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    const check = () => healthCheck().then(() => setHealth(true)).catch(() => setHealth(false));
    check();
    const id = setInterval(check, 30_000);
    return () => clearInterval(id);
  }, []);

  /* Pre-load migrations for command palette search */
  useEffect(() => {
    fetch("/api/v1/migrations/?limit=50")
      .then(r => r.ok ? r.json() : [])
      .then(setMigrations)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setCmdOpen(v => !v);
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, []);

  const crumbs = buildCrumbs(loc.pathname, t);

  return (
    <>
      <header style={{
        position: "sticky", top: 0,
        height: "var(--topbar-height)",
        background: "var(--surface-0)",
        borderBottom: "1px solid var(--surface-4)",
        display: "flex", alignItems: "center",
        padding: "0 24px",
        gap: 12,
        zIndex: 90,
        backdropFilter: "blur(8px)",
        WebkitBackdropFilter: "blur(8px)",
      }}>

        {/* Breadcrumb */}
        <nav style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12.5, flexShrink: 0 }}>
          {crumbs.map((c, i) => (
            <span key={i} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              {i > 0 && <ChevronRight size={11} style={{ color: "var(--text-disabled)" }} />}
              {c.to ? (
                <button
                  onClick={() => navigate(c.to)}
                  style={{
                    background: "none", border: "none", cursor: "pointer",
                    fontSize: 12.5, padding: 0,
                    color: i === crumbs.length - 1 ? "var(--text-primary)" : "var(--text-tertiary)",
                    fontWeight: i === crumbs.length - 1 ? 600 : 400,
                    transition: "color 120ms",
                  }}
                  onMouseEnter={e => e.currentTarget.style.color = "var(--brand-500)"}
                  onMouseLeave={e => e.currentTarget.style.color = i === crumbs.length - 1 ? "var(--text-primary)" : "var(--text-tertiary)"}
                >
                  {c.label}
                </button>
              ) : (
                <span style={{
                  color: i === crumbs.length - 1 ? "var(--text-primary)" : "var(--text-tertiary)",
                  fontWeight: i === crumbs.length - 1 ? 600 : 400,
                }}>{c.label}</span>
              )}
            </span>
          ))}
        </nav>

        {/* Command bar trigger */}
        <button
          onClick={() => setCmdOpen(true)}
          className="cp-command-trigger"
          style={{
            flex: 1, maxWidth: 320,
            display: "flex", alignItems: "center", gap: 8,
            padding: "6px 12px",
            borderRadius: 8,
            background: "var(--surface-2)",
            border: "1px solid var(--surface-4)",
            fontSize: 12.5, color: "var(--text-tertiary)",
            cursor: "pointer", transition: "all 150ms ease",
            textAlign: "left",
          }}
          onMouseEnter={e => { e.currentTarget.style.background = "var(--surface-3)"; e.currentTarget.style.borderColor = "var(--surface-5)"; }}
          onMouseLeave={e => { e.currentTarget.style.background = "var(--surface-2)"; e.currentTarget.style.borderColor = "var(--surface-4)"; }}
        >
          <Search size={13} />
          <span style={{ flex: 1 }}>{t("common.search")}</span>
          <kbd style={{
            padding: "1px 5px", borderRadius: 4,
            background: "var(--surface-0)", border: "1px solid var(--surface-5)",
            fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--text-disabled)",
          }}>⌘K</kbd>
        </button>

        {/* Right zone */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>

          <AlertsCenter />

          <div style={{ width: 1, height: 20, background: "var(--surface-4)" }} />

          {/* Health chip */}
          <div style={{
            display: "flex", alignItems: "center", gap: 5,
            padding: "4px 10px", borderRadius: 20,
            background: health === true ? "var(--success-subtle)" : health === false ? "var(--error-subtle)" : "var(--surface-2)",
            border: `1px solid ${health === true ? "var(--success-border)" : health === false ? "var(--error-border)" : "var(--surface-4)"}`,
            fontSize: 11.5, fontWeight: 600,
            color: health === true ? "var(--success-text)" : health === false ? "var(--error-text)" : "var(--text-tertiary)",
            transition: "all 300ms ease",
          }}>
            <span style={{
              width: 6, height: 6, borderRadius: "50%",
              background: health === true ? "#22c55e" : health === false ? "#ef4444" : "#9ca3af",
              boxShadow: health === true ? "0 0 6px #22c55e" : health === false ? "0 0 6px #ef4444" : "none",
              animation: health === true ? "pulse 2s infinite" : "none",
              flexShrink: 0,
            }} />
            {health === true ? t("settings.about.healthy") : health === false ? t("settings.about.offline") : "…"}
          </div>

          <div style={{ width: 1, height: 20, background: "var(--surface-4)" }} />

          {/* Theme toggle */}
          <button
            onClick={toggle}
            title={theme === "dark" ? t("settings.theme.light") : t("settings.theme.dark")}
            style={{
              width: 32, height: 32,
              display: "flex", alignItems: "center", justifyContent: "center",
              borderRadius: 8, cursor: "pointer",
              background: "transparent", border: "1px solid transparent",
              color: "var(--text-tertiary)", transition: "all 150ms ease",
            }}
            onMouseEnter={e => { e.currentTarget.style.background = "var(--surface-2)"; e.currentTarget.style.borderColor = "var(--surface-4)"; }}
            onMouseLeave={e => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.borderColor = "transparent"; }}
          >
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>

          <div style={{ width: 1, height: 20, background: "var(--surface-4)" }} />

          {/* User avatar + dropdown */}
          {user && (
            <div ref={userMenuRef} style={{ position: "relative" }}>
              <button
                onClick={() => setUserMenuOpen(v => !v)}
                style={{
                  display: "flex", alignItems: "center", gap: 8,
                  padding: "4px 8px 4px 4px", borderRadius: 20,
                  background: userMenuOpen ? "var(--surface-2)" : "transparent",
                  border: "1px solid", borderColor: userMenuOpen ? "var(--surface-4)" : "transparent",
                  cursor: "pointer", transition: "all 150ms",
                }}
                onMouseEnter={e => { e.currentTarget.style.background = "var(--surface-2)"; e.currentTarget.style.borderColor = "var(--surface-4)"; }}
                onMouseLeave={e => { if (!userMenuOpen) { e.currentTarget.style.background = "transparent"; e.currentTarget.style.borderColor = "transparent"; } }}
              >
                {/* Avatar circle */}
                <div style={{
                  width: 28, height: 28, borderRadius: "50%",
                  background: "linear-gradient(135deg, #4f46e5, #0284c7)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 12, fontWeight: 700, color: "#fff", flexShrink: 0,
                }}>
                  {(user.username || user.email || "?")[0].toUpperCase()}
                </div>
                <div style={{ textAlign: "left" }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)", lineHeight: 1.2 }}>
                    {user.username || user.email?.split("@")[0]}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-tertiary)", textTransform: "capitalize" }}>
                    {user.role === "admin" ? t("users.role.admin") : t("users.role.analyst")}
                  </div>
                </div>
              </button>

              {/* Dropdown menu */}
              <AnimatePresence>
                {userMenuOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -6, scale: 0.97 }}
                    animate={{ opacity: 1, y: 0,  scale: 1 }}
                    exit={{   opacity: 0, y: -4, scale: 0.97 }}
                    transition={{ duration: 0.15 }}
                    style={{
                      position: "absolute", right: 0, top: "calc(100% + 8px)",
                      width: 180, borderRadius: 10,
                      background: "var(--surface-0)",
                      border: "1px solid var(--surface-4)",
                      boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
                      overflow: "hidden", zIndex: 200,
                    }}
                  >
                    <Link
                      to="/profile"
                      onClick={() => setUserMenuOpen(false)}
                      style={{
                        display: "flex", alignItems: "center", gap: 10,
                        padding: "10px 14px", fontSize: 13, fontWeight: 500,
                        color: "var(--text-primary)", textDecoration: "none",
                        transition: "background 100ms",
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = "var(--surface-2)"}
                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                    >
                      <User size={14} style={{ color: "var(--text-tertiary)" }} />
                      {t("nav.profile")}
                    </Link>
                    <div style={{ height: 1, background: "var(--surface-4)", margin: "0 10px" }} />
                    <button
                      onClick={() => { logout(); navigate("/login"); setUserMenuOpen(false); }}
                      style={{
                        width: "100%", display: "flex", alignItems: "center", gap: 10,
                        padding: "10px 14px", fontSize: 13, fontWeight: 500,
                        color: "var(--error-text)", background: "none", border: "none",
                        cursor: "pointer", textAlign: "left", transition: "background 100ms",
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = "var(--error-subtle)"}
                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                    >
                      <LogOut size={14} />
                      {t("nav.logout")}
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}
        </div>
      </header>

      {/* Command Palette */}
      <CommandPalette
        open={cmdOpen}
        onClose={() => setCmdOpen(false)}
        migrations={migrations}
      />
    </>
  );
}
