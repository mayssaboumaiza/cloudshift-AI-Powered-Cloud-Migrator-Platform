/**
 * WarningsBanner — Yellow banner shown when state.warnings is non-empty.
 * Does not block migration — purely informational.
 */

import { AlertTriangle, X } from "lucide-react";
import { useState } from "react";

export default function WarningsBanner({ warnings = [] }) {
  const [dismissed, setDismissed] = useState(false);

  if (!warnings?.length || dismissed) return null;

  return (
    <div style={{
      marginBottom: 16, padding: "10px 14px",
      background: "#2d1a00", borderRadius: 8,
      border: "1px solid #f59e0b",
      display: "flex", alignItems: "flex-start", gap: 10,
    }}>
      <AlertTriangle size={16} color="#fbbf24" style={{ flexShrink: 0, marginTop: 1 }} />

      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#fbbf24", marginBottom: 4 }}>
          Avertissements ({warnings.length})
        </div>
        <ul style={{ margin: 0, paddingLeft: 16 }}>
          {warnings.map((w, i) => (
            <li key={i} style={{ fontSize: 11, color: "#d97706", marginBottom: 2 }}>
              {typeof w === "string" ? w : w.message || JSON.stringify(w)}
            </li>
          ))}
        </ul>
        <div style={{ fontSize: 10, color: "#78350f", marginTop: 6 }}>
          Ces avertissements ne bloquent pas la migration.
        </div>
      </div>

      <button
        onClick={() => setDismissed(true)}
        style={{
          background: "none", border: "none", cursor: "pointer",
          color: "#92400e", padding: 2, flexShrink: 0,
        }}
        title="Masquer"
      >
        <X size={14} />
      </button>
    </div>
  );
}
