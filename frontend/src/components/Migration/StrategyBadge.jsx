/**
 * StrategyBadge — 7R strategy badge with certainty indicator.
 *
 * certainty="high"    → green   [REHOST 95%]
 * certainty="medium"  → orange  [REHOST 75%] + ask_human icon
 * tie_detected=true   → blue    [REHOST / REPLATFORM]
 */

const STRATEGY_COLORS = {
  REHOST:     { color: "#60a5fa", bg: "#172554" },
  REPLATFORM: { color: "#34d399", bg: "#052e16" },
  REFACTOR:   { color: "#f59e0b", bg: "#2d1a00" },
  RETIRE:     { color: "#9ca3af", bg: "#1f2937" },
  RETAIN:     { color: "#6b7280", bg: "#111827" },
  REPURCHASE: { color: "#a78bfa", bg: "#1e1b4b" },
  RELOCATE:   { color: "#38bdf8", bg: "#082f49" },
};

const CERTAINTY_BORDER = {
  high:   "2px solid transparent",
  medium: "2px solid #f59e0b",
  low:    "2px solid #ef4444",
};

export default function StrategyBadge({
  strategy = "REHOST",
  confidence = null,
  certainty = "high",
  tieDetected = false,
  tieAlternative = null,
  triggerAskHuman = false,
  size = "md",
}) {
  const sc = STRATEGY_COLORS[strategy?.toUpperCase()] || { color: "#9ca3af", bg: "#1f2937" };
  const border = tieDetected
    ? "2px solid #38bdf8"
    : CERTAINTY_BORDER[certainty] || CERTAINTY_BORDER.high;

  const pct = confidence != null ? Math.round(confidence * 100) : null;
  const fontSize = size === "sm" ? 10 : 12;

  const label = tieDetected && tieAlternative
    ? `${strategy} / ${tieAlternative}`
    : strategy?.toUpperCase();

  const icon = tieDetected
    ? "🔀"
    : certainty === "medium" || triggerAskHuman
    ? "⚠️"
    : certainty === "high"
    ? "✅"
    : "";

  const title = tieDetected
    ? `Égalité détectée entre ${strategy} et ${tieAlternative} — confirmation humaine requise`
    : triggerAskHuman
    ? `Zone grise — confirmation humaine recommandée (confidence ${pct ?? "?"}%)`
    : `${strategy} — confidence ${pct ?? "?"}%`;

  return (
    <span
      title={title}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 8,
        fontSize,
        fontWeight: 700,
        color: tieDetected ? "#38bdf8" : sc.color,
        background: tieDetected ? "#082f49" : sc.bg,
        border,
        cursor: "default",
        whiteSpace: "nowrap",
      }}
    >
      {icon && <span style={{ fontSize: fontSize - 1 }}>{icon}</span>}
      {label}
      {pct != null && (
        <span style={{ fontSize: fontSize - 1, fontWeight: 500, opacity: 0.8 }}>
          {pct}%
        </span>
      )}
    </span>
  );
}
