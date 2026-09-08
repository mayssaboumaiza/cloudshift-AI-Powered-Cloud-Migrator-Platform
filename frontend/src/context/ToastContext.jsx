import { createContext, useContext, useState, useCallback } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle, AlertCircle, Info, AlertTriangle, X } from "lucide-react";

const ToastContext = createContext(null);

const TOAST_CONFIG = {
  success: {
    icon: CheckCircle,
    bg: "var(--success-subtle)",
    border: "var(--success-border)",
    iconColor: "var(--success-text)",
    barColor: "var(--success-solid)",
  },
  error: {
    icon: AlertCircle,
    bg: "var(--error-subtle)",
    border: "var(--error-border)",
    iconColor: "var(--error-text)",
    barColor: "var(--error-solid)",
  },
  warning: {
    icon: AlertTriangle,
    bg: "var(--warning-subtle)",
    border: "var(--warning-border)",
    iconColor: "var(--warning-text)",
    barColor: "var(--warning-solid)",
  },
  info: {
    icon: Info,
    bg: "var(--info-subtle)",
    border: "var(--info-border)",
    iconColor: "var(--info-text)",
    barColor: "var(--info-solid)",
  },
};

function ToastItem({ toast, onRemove }) {
  const cfg = TOAST_CONFIG[toast.type] || TOAST_CONFIG.info;
  const Icon = cfg.icon;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 32, scale: 0.93 }}
      animate={{ opacity: 1, y: 0,  scale: 1 }}
      exit={{   opacity: 0, y: 12, scale: 0.95 }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      style={{
        position: "relative",
        display: "flex",
        alignItems: "flex-start",
        gap: 11,
        padding: "12px 14px 14px",
        borderRadius: 11,
        background: cfg.bg,
        border: `1px solid ${cfg.border}`,
        boxShadow: "0 4px 20px rgba(0,0,0,0.09), 0 1px 4px rgba(0,0,0,0.06)",
        minWidth: 280,
        maxWidth: 380,
        overflow: "hidden",
      }}
    >
      <Icon size={16} style={{ color: cfg.iconColor, marginTop: 1, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        {toast.title && (
          <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)", marginBottom: 2 }}>
            {toast.title}
          </div>
        )}
        <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.45 }}>
          {toast.message}
        </div>
      </div>
      <button
        onClick={() => onRemove(toast.id)}
        style={{
          background: "none", border: "none", cursor: "pointer",
          color: "var(--text-disabled)", padding: 2, borderRadius: 4,
          display: "flex", alignItems: "center", flexShrink: 0,
          transition: "color 120ms",
        }}
        onMouseEnter={e => e.currentTarget.style.color = "var(--text-secondary)"}
        onMouseLeave={e => e.currentTarget.style.color = "var(--text-disabled)"}
      >
        <X size={13} />
      </button>
      {/* Progress bar */}
      <motion.div
        initial={{ scaleX: 1 }}
        animate={{ scaleX: 0 }}
        transition={{ duration: (toast.duration || 4000) / 1000, ease: "linear" }}
        style={{
          position: "absolute", bottom: 0, left: 0,
          height: 2, width: "100%",
          background: cfg.barColor,
          transformOrigin: "left",
          borderRadius: "0 0 0 11px",
          opacity: 0.6,
        }}
      />
    </motion.div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const addToast = useCallback((message, type = "info", options = {}) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const duration = options.duration ?? 4000;
    const toast = { id, message, type, duration, title: options.title };
    setToasts(prev => [...prev.slice(-4), toast]); // max 5 toasts
    setTimeout(() => removeToast(id), duration + 500);
    return id;
  }, [removeToast]);

  const toast = {
    success: (msg, opts) => addToast(msg, "success", opts),
    error:   (msg, opts) => addToast(msg, "error",   opts),
    warning: (msg, opts) => addToast(msg, "warning", opts),
    info:    (msg, opts) => addToast(msg, "info",    opts),
  };

  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div style={{
        position: "fixed",
        bottom: 24,
        right: 24,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column-reverse",
        gap: 8,
        pointerEvents: "none",
      }}>
        <AnimatePresence mode="popLayout">
          {toasts.map(t => (
            <div key={t.id} style={{ pointerEvents: "all" }}>
              <ToastItem toast={t} onRemove={removeToast} />
            </div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
