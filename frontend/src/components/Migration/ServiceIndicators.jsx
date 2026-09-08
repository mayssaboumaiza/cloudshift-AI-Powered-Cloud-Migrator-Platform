/**
 * ServiceIndicators — Budget / region / complexity indicators per service card.
 *
 * Budget  : amount + source (live/fallback) + consumption bar
 * Region  : badge (available/preview/nearest/unavailable) + source
 * Complexity: bar + label (Faible/Moyenne/Élevée)
 */

const REGION_BADGE = {
  available:   { color: "#34d399", bg: "#052e16", border: "#10b981", label: "Disponible" },
  ga:          { color: "#34d399", bg: "#052e16", border: "#10b981", label: "GA" },
  preview:     { color: "#fbbf24", bg: "#2d1a00", border: "#f59e0b", label: "Preview" },
  unavailable: { color: "#f87171", bg: "#1a0000", border: "#dc2626", label: "Indisponible" },
  neighbor:    { color: "#a78bfa", bg: "#1e1b4b", border: "#7c3aed", label: "Région proche" },
};

const COMPLEXITY_LABEL = { HIGH: "Élevée", MEDIUM: "Moyenne", LOW: "Faible" };
const COMPLEXITY_COLOR  = { HIGH: "#f87171", MEDIUM: "#fbbf24", LOW: "#34d399" };
const COMPLEXITY_WIDTH  = { HIGH: "100%",    MEDIUM: "60%",     LOW: "30%"    };

function BudgetBar({ cost, budget, source }) {
  if (cost == null || cost <= 0) return null;
  const pct    = budget > 0 ? Math.min((cost / budget) * 100, 100) : 0;
  const over   = budget > 0 && cost > budget;
  const barCol = over ? "#f87171" : "#34d399";

  return (
    <div style={{ fontSize: 11, marginBottom: 4 }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 2,
      }}>
        <span style={{ color: "#6b7280" }}>Budget</span>
        <span style={{
          fontWeight: 700,
          color: over ? "#f87171" : "#34d399",
        }}>
          {cost.toFixed(0)} €/mois
          {over && <span style={{ marginLeft: 3, fontSize: 10 }}>⚠️</span>}
        </span>
      </div>
      {budget > 0 && (
        <div style={{
          height: 4, borderRadius: 2, background: "#1f2937", overflow: "hidden",
        }}>
          <div style={{
            width: `${pct}%`, height: "100%",
            background: barCol, borderRadius: 2,
          }} />
        </div>
      )}
      {source && (
        <div style={{ fontSize: 9, color: "#374151", marginTop: 1 }}>
          Source: {source}
        </div>
      )}
    </div>
  );
}

function RegionBadge({ regionFit, regionLabel, regionSource }) {
  const key    = (regionLabel || "").toLowerCase().includes("nearest") ? "neighbor"
               : regionFit === 1.0 ? "available"
               : regionFit === 0.8 ? "preview"
               : regionFit === 0.0 ? "unavailable"
               : "available";
  const config = REGION_BADGE[key] || REGION_BADGE.available;

  return (
    <div style={{ fontSize: 11, marginBottom: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: "#6b7280" }}>Région</span>
        <span style={{
          padding: "1px 7px", borderRadius: 6, fontSize: 10, fontWeight: 600,
          color: config.color, background: config.bg,
          border: `1px solid ${config.border}`,
        }}>
          {regionLabel || config.label}
        </span>
      </div>
      {regionSource && (
        <div style={{ fontSize: 9, color: "#374151", marginTop: 1 }}>
          Source: {regionSource}
        </div>
      )}
    </div>
  );
}

function ComplexityBar({ complexity }) {
  const c = (complexity || "LOW").toUpperCase();
  return (
    <div style={{ fontSize: 11 }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 2,
      }}>
        <span style={{ color: "#6b7280" }}>Complexité</span>
        <span style={{ fontWeight: 600, color: COMPLEXITY_COLOR[c] || "#9ca3af" }}>
          {COMPLEXITY_LABEL[c] || c}
        </span>
      </div>
      <div style={{ height: 4, borderRadius: 2, background: "#1f2937", overflow: "hidden" }}>
        <div style={{
          width: COMPLEXITY_WIDTH[c] || "50%", height: "100%",
          background: COMPLEXITY_COLOR[c] || "#9ca3af", borderRadius: 2,
        }} />
      </div>
    </div>
  );
}

export default function ServiceIndicators({
  monthlyCost,
  totalBudget,
  costSource,
  regionFit,
  regionLabel,
  regionSource,
  complexity,
}) {
  return (
    <div style={{
      marginTop: 8, padding: "8px 10px",
      background: "#111827", borderRadius: 6,
      border: "1px solid #1f2937",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <BudgetBar cost={monthlyCost} budget={totalBudget} source={costSource} />
      <RegionBadge regionFit={regionFit} regionLabel={regionLabel} regionSource={regionSource} />
      <ComplexityBar complexity={complexity} />
    </div>
  );
}
