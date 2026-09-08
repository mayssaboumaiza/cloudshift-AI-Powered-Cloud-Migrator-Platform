import { NavLink, useLocation, Link, useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
import {
  LayoutDashboard, PlusCircle, Settings, Activity,
  BotMessageSquare, BarChart2,
  Globe, ChevronRight, Cpu, Shield, Zap,
  Users, LogOut, ChevronDown,
} from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { useI18n } from "../../context/I18nContext";

// Nav sections are built dynamically using t() inside the component
// so language changes take effect immediately without page reload.
const ADMIN_LINK = { to: "/admin/users", icon: Users, labelKey: "nav.users" };

/* ── Platform edge badges — labels via t() inside component ─────────────── */
const EDGE_PILL_DEFS = [
  { icon: Cpu,    labelKey: "sidebar.pill.ai",      color: "#7C3AED", bg: "#F5F3FF" },
  { icon: Globe,  labelKey: "sidebar.pill.cloud",   color: "#0284C7", bg: "#F0F9FF" },
  { icon: Shield, labelKey: "sidebar.pill.gdpr",    color: "#15803D", bg: "#F0FDF4" },
  { icon: Zap,    labelKey: "sidebar.pill.self",    color: "#D97706", bg: "#FFFBEB" },
];

function CloudShiftLogo({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ display: "block" }}>
      <defs>
        <linearGradient id="logoGrad" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="#6366F1" />
          <stop offset="100%" stopColor="#0EA5E9" />
        </linearGradient>
        <linearGradient id="logoGrad2" x1="0" y1="0" x2="32" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0%"   stopColor="#6366F1" />
          <stop offset="100%" stopColor="#0EA5E9" />
        </linearGradient>
      </defs>
      {/* Cloud shape — source cloud (left, slightly faded) */}
      <path
        d="M10 7C7.2 7 5 9.2 5 12c0 .4 0 .7.1 1C3.9 13.5 3 14.6 3 16c0 1.9 1.6 3.5 3.5 3.5H13V7.3C12.1 7.1 11 7 10 7Z"
        fill="url(#logoGrad)" opacity="0.45"
      />
      {/* Cloud shape — target cloud (right, full color) */}
      <path
        d="M22 7c-1 0-2 .1-2.9.4V19.5h6.4C27.4 19.5 29 17.9 29 16c0-1.4-.9-2.5-2.1-3 .1-.3.1-.6.1-1 0-2.8-2.2-5-5-5Z"
        fill="url(#logoGrad)" opacity="0.9"
      />
      {/* Migration arrow — center, bold */}
      <path
        d="M13 16h6M16 13l3 3-3 3"
        stroke="url(#logoGrad2)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      />
      {/* AI spark — top right of target cloud */}
      <circle cx="26" cy="8" r="2.2" fill="#6366F1" opacity="0.85"/>
      <path d="M26 6.2V7m0 2v.8M24.2 8H25m2 0h.8M24.8 6.8l.5.5m1.4 1.4.5.5M27.2 6.8l-.5.5m-1.4 1.4-.5.5"
        stroke="white" strokeWidth="0.7" strokeLinecap="round"
      />
    </svg>
  );
}

function SidebarStats() {
  const { t } = useI18n();
  const [stats, setStats] = useState({ active: 0, completed: 0, total: 0 });

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch("/api/v1/migrations/?limit=100");
        if (!res.ok) return;
        const list = await res.json();
        setStats({
          active:    list.filter(m => !["Completed","Rejected","Exported","Failed","Analysis_Failed"].includes(m.status)).length,
          completed: list.filter(m => ["Completed","Exported"].includes(m.status)).length,
          total:     list.length,
        });
      } catch (_) {}
    };
    fetchStats();
    const id = setInterval(fetchStats, 60_000);
    return () => clearInterval(id);
  }, []);

  return (
    <div style={{
      margin: "8px 10px",
      padding: "10px 12px",
      background: "var(--surface-3)",
      borderRadius: 10,
      border: "1px solid var(--sidebar-border)",
      display: "grid",
      gridTemplateColumns: "1fr 1fr 1fr",
      gap: 4,
    }}>
      {[
        { label: t("sidebar.stats.total"), value: stats.total,     color: "var(--text-primary)"  },
        { label: t("sidebar.stats.active"),value: stats.active,    color: "var(--brand-500)"     },
        { label: t("sidebar.stats.done"),  value: stats.completed, color: "#16A34A"              },
      ].map(({ label, value, color }) => (
        <div key={label} style={{ textAlign: "center" }}>
          <div style={{ fontSize: 16, fontWeight: 800, color, lineHeight: 1 }}>{value}</div>
          <div style={{
            fontSize: 9, color: "var(--sidebar-section-label)",
            marginTop: 2, textTransform: "uppercase", letterSpacing: "0.04em",
          }}>{label}</div>
        </div>
      ))}
    </div>
  );
}

function SidebarLink({ link }) {
  const location = useLocation();
  const isActive = !link.disabled && (
    link.to === "/dashboard"
      ? location.pathname === "/dashboard"
      : location.pathname.startsWith(link.to)
  );

  const Icon = link.icon;

  return (
    <NavLink
      to={link.disabled ? "#" : link.to}
      style={() => ({
        display: "flex",
        alignItems: "center",
        gap: 9,
        padding: "8px 12px",
        borderRadius: 8,
        fontSize: 13,
        fontWeight: isActive ? 600 : 450,
        color: isActive
          ? "var(--sidebar-active-text)"
          : link.disabled
            ? "var(--sidebar-section-label)"
            : "var(--sidebar-text)",
        textDecoration: "none",
        cursor: link.disabled ? "default" : "pointer",
        pointerEvents: link.disabled ? "none" : "auto",
        background: isActive ? "var(--sidebar-active-bg)" : "transparent",
        borderLeft: isActive ? "2px solid var(--brand-500)" : "2px solid transparent",
        marginLeft: isActive ? -2 : 0,
        transition: "all 150ms cubic-bezier(0.16,1,0.3,1)",
        position: "relative",
        overflow: "hidden",
      })}
      onMouseEnter={link.disabled ? undefined : (e) => {
        if (!isActive) {
          e.currentTarget.style.background = "var(--surface-3)";
          e.currentTarget.style.color = "var(--sidebar-text-hover)";
        }
      }}
      onMouseLeave={link.disabled ? undefined : (e) => {
        if (!isActive) {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = link.disabled ? "var(--sidebar-section-label)" : "var(--sidebar-text)";
        }
      }}
      tabIndex={link.disabled ? -1 : 0}
    >
      <Icon
        size={15}
        strokeWidth={isActive ? 2.2 : 1.8}
        style={{ color: isActive ? "var(--brand-500)" : "inherit", flexShrink: 0 }}
      />
      <span style={{ flex: 1 }}>{link.label}</span>
      {link.badge && (
        <span style={{
          padding: "1px 6px", borderRadius: 10, fontSize: 9, fontWeight: 800,
          background: "#F97316", color: "#fff",
        }}>{link.badge}</span>
      )}
      {link.disabled && (
        <span style={{
          padding: "1px 5px", borderRadius: 6, fontSize: 8.5, fontWeight: 600,
          background: "var(--surface-4)", color: "var(--text-disabled)",
        }}>{link.soonLabel}</span>
      )}
      {isActive && !link.disabled && (
        <ChevronRight size={11} style={{ color: "var(--brand-500)", flexShrink: 0 }} />
      )}
    </NavLink>
  );
}

const ROLE_STYLE_BASES = {
  admin:    { bg: "#F5F3FF", color: "#7C3AED", border: "#DDD6FE", labelKey: "sidebar.role.admin"    },
  analyst:  { bg: "#EFF6FF", color: "#1D6FD1", border: "#BFDBFE", labelKey: "sidebar.role.engineer" },
  engineer: { bg: "#EFF6FF", color: "#1D6FD1", border: "#BFDBFE", labelKey: "sidebar.role.engineer" },
  viewer:   { bg: "#F0FDF4", color: "#15803D", border: "#BBF7D0", labelKey: "sidebar.role.viewer"   },
};

export default function Sidebar() {
  const { user, logout, isAdmin } = useAuth();
  const navigate = useNavigate();
  const { t } = useI18n();

  const NAV_SECTIONS = [
    {
      label: t("sidebar.section.workspace"),
      links: [
        { to: "/dashboard",    label: t("nav.migrations"),  icon: LayoutDashboard },
        { to: "/analytics",    label: t("nav.analytics"),   icon: BarChart2       },
        { to: "/activity",     label: t("nav.activity"),    icon: Activity        },
      ],
    },
    {
      label: t("sidebar.section.intelligence"),
      links: [
        { to: "/assistant",    label: t("nav.assistant"),   icon: BotMessageSquare },
      ],
    },
    {
      label: t("sidebar.section.system"),
      links: [
        { to: "/settings",     label: t("nav.settings"),    icon: Settings },
      ],
    },
  ];

  const soonLabel = t("sidebar.badge.soon");

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <aside style={{
      position: "fixed",
      top: 0, left: 0, bottom: 0,
      width: "var(--sidebar-width)",
      background: "var(--sidebar-bg)",
      borderRight: "1px solid var(--sidebar-border)",
      display: "flex",
      flexDirection: "column",
      zIndex: 100,
      overflow: "hidden",
    }}>

      {/* Brand */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "18px 16px 16px",
        borderBottom: "1px solid var(--sidebar-border)",
        flexShrink: 0,
      }}>
        <div style={{
          width: 36, height: 36,
          background: "linear-gradient(135deg, rgba(99,102,241,0.12) 0%, rgba(14,165,233,0.18) 100%)",
          border: "1px solid var(--sidebar-border)",
          borderRadius: 10,
          display: "flex", alignItems: "center", justifyContent: "center",
          flexShrink: 0,
        }}>
          <CloudShiftLogo size={20} />
        </div>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <span style={{
            fontSize: 15, lineHeight: 1,
            background: "linear-gradient(to right, #6366F1, #0EA5E9)",
            WebkitBackgroundClip: "text",
            WebkitTextFillColor: "transparent",
            backgroundClip: "text",
            fontWeight: 800,
            letterSpacing: "-0.02em",
          }}>
            CloudShift
          </span>
          <span style={{
            fontSize: 9.5,
            color: "var(--sidebar-section-label)",
            marginTop: 2,
            textTransform: "uppercase",
            letterSpacing: "0.07em",
          }}>
            {t("sidebar.tagline")}
          </span>
        </div>
        <div style={{
          marginLeft: "auto",
          width: 7, height: 7, borderRadius: "50%",
          background: "#16A34A",
          boxShadow: "0 0 6px #16A34A",
          animation: "pulse 2s ease infinite",
          flexShrink: 0,
        }} />
      </div>

      {/* Stats */}
      <SidebarStats />

      {/* Nav */}
      <div style={{ padding: "4px 8px", flex: 1, overflowY: "auto", scrollbarWidth: "none" }}>
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} style={{ marginBottom: 8 }}>
            <span style={{
              display: "block", fontSize: 9.5, fontWeight: 700,
              textTransform: "uppercase", letterSpacing: "0.11em",
              color: "var(--sidebar-section-label)",
              padding: "6px 12px 3px",
            }}>
              {section.label}
            </span>
            <nav style={{ display: "flex", flexDirection: "column", gap: 1 }}>
              {section.links.map((l) => <SidebarLink key={l.to + l.label} link={{ ...l, soonLabel }} />)}
            </nav>
          </div>
        ))}

        {/* Admin section — visible only to admins */}
        {isAdmin && isAdmin() && (
          <div style={{ marginBottom: 8 }}>
            <span style={{
              fontSize: 9.5, fontWeight: 700,
              textTransform: "uppercase", letterSpacing: "0.11em",
              color: "#7C3AED", padding: "6px 12px 3px",
              display: "flex", alignItems: "center", gap: 5,
            }}>
              <Shield size={9} style={{ color: "#7C3AED" }} /> {t("sidebar.admin.section")}
            </span>
            <nav style={{ display: "flex", flexDirection: "column", gap: 1 }}>
              <SidebarLink key={ADMIN_LINK.to} link={{ ...ADMIN_LINK, label: t(ADMIN_LINK.labelKey), soonLabel }} />
            </nav>
          </div>
        )}
      </div>

      {/* Platform edge mini-badges */}
      <div style={{
        margin: "4px 10px",
        padding: "10px 10px 8px",
        borderRadius: 9,
        background: "var(--surface-3)",
        border: "1px solid var(--sidebar-border)",
      }}>
        <div style={{
          fontSize: 8.5, fontWeight: 700, textTransform: "uppercase",
          letterSpacing: "0.1em", color: "var(--text-disabled)",
          marginBottom: 7,
        }}>
          {t("sidebar.capabilities")}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
          {EDGE_PILL_DEFS.map(p => (
            <div key={p.labelKey} style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "4px 7px", borderRadius: 6,
              background: p.bg, border: `1px solid ${p.color}25`,
            }}>
              <p.icon size={10} style={{ color: p.color, flexShrink: 0 }} />
              <span style={{ fontSize: 9.5, fontWeight: 600, color: p.color }}>{t(p.labelKey)}</span>
            </div>
          ))}
        </div>
      </div>

      {/* New Migration CTA */}
      <div style={{ padding: "8px 10px" }}>
        <Link
          to="/migrations/new"
          style={{
            display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
            padding: "9px 12px", borderRadius: 9, textDecoration: "none",
            background: "linear-gradient(135deg, #6366F1 0%, var(--brand-500) 100%)",
            color: "#fff", fontSize: 12.5, fontWeight: 700,
            boxShadow: "0 2px 8px rgba(14,165,233,0.25)",
            transition: "opacity 150ms",
          }}
          onMouseEnter={e => e.currentTarget.style.opacity = "0.9"}
          onMouseLeave={e => e.currentTarget.style.opacity = "1"}
        >
          <PlusCircle size={14} strokeWidth={2.2} />
          {t("nav.new")}
        </Link>
      </div>

      {/* User footer */}
      {user ? (
        <div style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--sidebar-border)",
          flexShrink: 0,
        }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 9,
            padding: "8px 10px", borderRadius: 9,
            background: "var(--surface-2)",
            border: "1px solid var(--sidebar-border)",
          }}>
            {/* Avatar — click → profile */}
            <Link to="/profile" title={t("nav.profile")} style={{ textDecoration: "none", flexShrink: 0 }}>
              <div style={{
                width: 30, height: 30, borderRadius: "50%",
                background: "linear-gradient(135deg, #6366f1, #0ea5e9)",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 12, fontWeight: 700, color: "#fff",
                transition: "transform 150ms, box-shadow 150ms",
                cursor: "pointer",
              }}
                onMouseEnter={e => { e.currentTarget.style.transform = "scale(1.1)"; e.currentTarget.style.boxShadow = "0 0 0 2px rgba(99,102,241,0.5)"; }}
                onMouseLeave={e => { e.currentTarget.style.transform = "scale(1)"; e.currentTarget.style.boxShadow = "none"; }}
              >
                {(user.username || user.email || "U")[0].toUpperCase()}
              </div>
            </Link>
            {/* Info — click → profile */}
            <Link to="/profile" style={{ flex: 1, minWidth: 0, textDecoration: "none" }}>
              <div style={{
                fontSize: 12, fontWeight: 600, color: "var(--text-primary)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {user.username || user.email}
              </div>
              {(() => {
                const rs = ROLE_STYLE_BASES[user.role] || ROLE_STYLE_BASES.viewer;
                return (
                  <span style={{
                    fontSize: 9.5, fontWeight: 700,
                    padding: "1px 6px", borderRadius: 8,
                    background: rs.bg, color: rs.color, border: `1px solid ${rs.border}`,
                  }}>
                    {t(rs.labelKey)}
                  </span>
                );
              })()}
            </Link>
            {/* Logout */}
            <button
              onClick={handleLogout}
              title={t("nav.logout")}
              style={{
                background: "none", border: "none", cursor: "pointer",
                padding: 5, borderRadius: 6, color: "var(--text-tertiary)",
                display: "flex", alignItems: "center", flexShrink: 0,
                transition: "color 150ms, background 150ms",
              }}
              onMouseEnter={e => { e.currentTarget.style.color = "#dc2626"; e.currentTarget.style.background = "#fef2f2"; }}
              onMouseLeave={e => { e.currentTarget.style.color = "var(--text-tertiary)"; e.currentTarget.style.background = "none"; }}
            >
              <LogOut size={13} />
            </button>
          </div>
        </div>
      ) : (
        <div style={{
          padding: "10px 14px",
          borderTop: "1px solid var(--sidebar-border)",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          flexShrink: 0,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#16A34A", boxShadow: "0 0 6px #16A34A", animation: "pulse 2s ease infinite" }} />
            <span style={{ fontSize: 10.5, color: "var(--sidebar-section-label)" }}>FastAPI · LangGraph</span>
          </div>
          <span style={{ fontSize: 9.5, color: "var(--sidebar-section-label)", background: "var(--surface-3)", padding: "2px 6px", borderRadius: 5, border: "1px solid var(--sidebar-border)" }}>v4.0</span>
        </div>
      )}
    </aside>
  );
}
