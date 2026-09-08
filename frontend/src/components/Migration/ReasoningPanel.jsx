/**
 * ReasoningPanel — Expandable scoring explanation for a single service.
 *
 * Shows:
 * - Global SAW score
 * - Progress bar per criterion (proportional to contribution)
 * - AHP weight + source per criterion
 * - why_not_rehost / why_not_refactor
 * - CR = 0.0175 ✅
 */

import { useState } from "react";
import { ChevronDown, ChevronUp, Info } from "lucide-react";

const CRITERION_META = {
  equivalence: {
    label:  "Équivalence Terraform",
    color:  "#60a5fa",
    source: "AWS MAP Framework — Gartner G00767542, 2022",
  },
  maturity: {
    label:  "Maturité du provider",
    color:  "#34d399",
    source: "HashiCorp Provider Tiers (official tier classification)",
  },
  budget_fit: {
    label:  "Budget fit",
    color:  "#fbbf24",
    source: "FinOps Foundation — 50% budget threshold",
  },
  region_fit: {
    label:  "Disponibilité région",
    color:  "#a78bfa",
    source: "RGPD Art.44-49 data residency obligations",
  },
  complexity: {
    label:  "Complexité (inversée)",
    color:  "#fb923c",
    source: "Agile Framework — sprint effort factor",
  },
};

function CriterionBar({ name, data, maxContrib }) {
  const meta = CRITERION_META[name] || { label: name, color: "#9ca3af", source: "" };
  const pct  = maxContrib > 0 ? (data.contribution / maxContrib) * 100 : 0;

  return (
    <div
      title={data.explanation || meta.source}
      style={{ marginBottom: 8, cursor: "help" }}
    >
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 3,
      }}>
        <span style={{ fontSize: 11, color: "#d1d5db" }}>
          {meta.label}
          <span style={{ color: "#6b7280", marginLeft: 4, fontSize: 10 }}>
            (w={data.weight?.toFixed(3) ?? "?"})
          </span>
        </span>
        <span style={{ fontSize: 11, fontWeight: 700, color: meta.color }}>
          +{data.contribution?.toFixed(4) ?? "0"}
        </span>
      </div>
      <div style={{
        height: 6, borderRadius: 3,
        background: "#1f2937", overflow: "hidden",
      }}>
        <div style={{
          width: `${pct}%`, height: "100%",
          background: meta.color, borderRadius: 3,
          transition: "width 0.3s ease",
        }} />
      </div>
      {meta.source && (
        <div style={{ fontSize: 9, color: "#4b5563", marginTop: 2 }}>
          Source: {meta.source}
        </div>
      )}
    </div>
  );
}

export default function ReasoningPanel({ service, reasoning, score, ahpCR }) {
  const [open, setOpen] = useState(false);

  if (!reasoning || typeof reasoning !== "object") return null;

  const criteria = Object.entries(reasoning);
  const maxContrib = Math.max(
    ...criteria.map(([, d]) => d?.contribution ?? 0),
    0.001,
  );

  return (
    <div style={{ marginTop: 6 }}>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex", alignItems: "center", gap: 4,
          fontSize: 11, color: "#60a5fa", background: "none",
          border: "none", cursor: "pointer", padding: 0,
        }}
      >
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        Voir l'explication du score
      </button>

      {open && (
        <div style={{
          marginTop: 8, padding: "12px 14px",
          background: "#111827", borderRadius: 8,
          border: "1px solid #1f2937",
        }}>
          {/* Header */}
          <div style={{
            display: "flex", justifyContent: "space-between",
            alignItems: "center", marginBottom: 12,
          }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: "#f9fafb" }}>
              Score SAW global :
              <span style={{ color: "#60a5fa", marginLeft: 6 }}>
                {score != null ? score.toFixed(4) : "—"}
              </span>
            </span>
            {ahpCR != null && (
              <span style={{
                fontSize: 10, padding: "2px 6px", borderRadius: 6,
                background: ahpCR < 0.10 ? "#052e16" : "#2d1a00",
                color:      ahpCR < 0.10 ? "#34d399" : "#f59e0b",
                border:     `1px solid ${ahpCR < 0.10 ? "#10b981" : "#f59e0b"}`,
              }}>
                CR = {ahpCR?.toFixed(4)} {ahpCR < 0.10 ? "✅" : "⚠️"}
              </span>
            )}
          </div>

          {/* Criterion bars */}
          {criteria.map(([name, data]) => (
            <CriterionBar
              key={name}
              name={name}
              data={data}
              maxContrib={maxContrib}
            />
          ))}

          {/* Why not alternatives */}
          {service?.why_not_rehost && (
            <div style={{
              marginTop: 10, padding: "6px 10px", borderRadius: 6,
              background: "#0f172a", borderLeft: "3px solid #f59e0b",
              fontSize: 11, color: "#fbbf24",
            }}>
              <strong>Pourquoi pas REHOST :</strong> {service.why_not_rehost}
            </div>
          )}
          {service?.why_not_refactor && (
            <div style={{
              marginTop: 6, padding: "6px 10px", borderRadius: 6,
              background: "#0f172a", borderLeft: "3px solid #60a5fa",
              fontSize: 11, color: "#93c5fd",
            }}>
              <strong>Pourquoi pas REFACTOR :</strong> {service.why_not_refactor}
            </div>
          )}

          {/* AHP note */}
          <div style={{
            marginTop: 10, fontSize: 10, color: "#4b5563",
            borderTop: "1px solid #1f2937", paddingTop: 6,
          }}>
            Poids AHP dérivés mathématiquement (Saaty 1980) — non arbitraires.
            {" "}<a
              href="/docs/scoring_methodology.md"
              style={{ color: "#374151", textDecoration: "underline" }}
              target="_blank" rel="noreferrer"
            >
              Voir méthodologie
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
