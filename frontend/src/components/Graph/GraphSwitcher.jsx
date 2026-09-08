import { useMemo, useState } from "react";
import { Network, Target } from "lucide-react";
import SourceGraph from "./SourceGraph";
import TargetGraph from "./TargetGraph";

const VIEWS = [
  { id: "source", label: "Source",  icon: Network },
  { id: "target", label: "Target",  icon: Target  },
];

export default function GraphSwitcher({ graph, plan, artifacts, sourceCloud, targetCloud }) {
  const [view, setView] = useState("source");

  const stats = useMemo(() => {
    const graphNodes = graph?.resources?.nodes ?? [];
    const graphEdges = graph?.resources?.edges ?? [];
    const resources  = plan?.resources ?? [];
    const targetSet  = new Set(
      resources
        .filter((r) => !["RETIRE", "RETAIN"].includes((r.strategy ?? "").toUpperCase()))
        .map((r) => r.target_service ?? r.target_equivalent)
        .filter(Boolean)
    );
    return {
      sourceNodes:    graphNodes.length,
      dependencies:   graphEdges.length,
      targetServices: targetSet.size,
      migrations:     resources.length,
    };
  }, [graph, plan]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--surface-0, #fff)",
        border: "1px solid var(--surface-3, #eef1f7)",
        borderRadius: 12,
        overflow: "hidden",
      }}
    >
      {/* ── Toolbar ─────────────────────────────────────────────────────── */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "10px 16px",
          borderBottom: "1px solid var(--surface-3, #eef1f7)",
          background: "var(--surface-1, #fafafa)",
          flexShrink: 0,
          gap: 12,
        }}
      >
        {/* Pill switcher */}
        <div
          style={{
            display: "inline-flex",
            background: "var(--surface-2, #f4f6fa)",
            borderRadius: 20,
            padding: 3,
            gap: 2,
          }}
        >
          {VIEWS.map((v) => {
            const active = view === v.id;
            return (
              <button
                key={v.id}
                onClick={() => setView(v.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "5px 16px",
                  borderRadius: 16,
                  border: "none",
                  cursor: "pointer",
                  fontSize: 12,
                  fontWeight: 600,
                  background: active ? "var(--surface-0, #fff)" : "transparent",
                  color: active ? "var(--text-primary, #0a0a0b)" : "var(--text-tertiary, #71717a)",
                  boxShadow: active ? "0 1px 4px rgba(0,0,0,0.10)" : "none",
                  transition: "all 140ms cubic-bezier(0.16,1,0.3,1)",
                  fontFamily: "var(--font-sans, system-ui)",
                  userSelect: "none",
                }}
              >
                <v.icon size={12} strokeWidth={2.3} />
                {v.label}
              </button>
            );
          })}
        </div>

        {/* Stats chips */}
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            gap: 16,
            fontSize: 11,
            color: "var(--text-tertiary, #71717a)",
            flexShrink: 0,
          }}
        >
          <StatChip label="Sources"   value={stats.sourceNodes} />
          <StatChip label="Deps"      value={stats.dependencies} />
          <StatChip label="Targets"   value={stats.targetServices} />
          <StatChip label="Migrations" value={stats.migrations} />
        </div>
      </div>

      {/* ── Graph canvas — both graphs stay mounted for zoom preservation ── */}
      <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
        <div
          style={{
            position: "absolute", inset: 0,
            opacity: view === "source" ? 1 : 0,
            transition: "opacity 200ms ease",
            pointerEvents: view === "source" ? "auto" : "none",
          }}
        >
          <SourceGraph
            graph={graph}
            artifacts={artifacts}
            sourceCloud={sourceCloud}
          />
        </div>

        <div
          style={{
            position: "absolute", inset: 0,
            opacity: view === "target" ? 1 : 0,
            transition: "opacity 200ms ease",
            pointerEvents: view === "target" ? "auto" : "none",
          }}
        >
          <TargetGraph
            plan={plan}
            graph={graph}
            artifacts={artifacts}
            targetCloud={targetCloud}
          />
        </div>
      </div>
    </div>
  );
}

function StatChip({ label, value }) {
  return (
    <span>
      <span
        style={{
          fontWeight: 700,
          color: "var(--text-primary, #0a0a0b)",
          marginRight: 3,
        }}
      >
        {value}
      </span>
      {label}
    </span>
  );
}
