/**
 * HumanInTheLoopPanel — Decision UI for gray zones and tie situations.
 *
 * trigger_ask_human=true → show two options with scores + confirm/reject buttons
 * tie_detected=true      → show "Scores trop proches" with score gap
 */

import { AlertTriangle, GitMerge } from "lucide-react";

export default function HumanInTheLoopPanel({
  service,
  strategy,
  tieDetected = false,
  tieGap = null,
  tieAlternative = null,
  scoreA = null,
  scoreB = null,
  uncertaintyReason = null,
  onConfirmA,
  onChooseB,
  onReject,
}) {
  const isTie = tieDetected && tieAlternative;

  return (
    <div style={{
      marginTop: 8, padding: "12px 14px",
      background: isTie ? "#082f49" : "#2d1a00",
      borderRadius: 8,
      border: `1px solid ${isTie ? "#0284c7" : "#f59e0b"}`,
    }}>
      {/* Header */}
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        marginBottom: 8,
      }}>
        {isTie
          ? <GitMerge size={14} color="#38bdf8" />
          : <AlertTriangle size={14} color="#f59e0b" />}
        <span style={{
          fontSize: 12, fontWeight: 700,
          color: isTie ? "#38bdf8" : "#fbbf24",
        }}>
          {isTie ? "Scores trop proches — confirmation requise" : "Zone grise — confirmation humaine recommandée"}
        </span>
      </div>

      {/* Reason */}
      {(uncertaintyReason || isTie) && (
        <p style={{ fontSize: 11, color: "#9ca3af", marginBottom: 10 }}>
          {isTie
            ? `Différence de score : ${tieGap != null ? tieGap.toFixed(4) : "?"} (seuil 0.05)`
            : uncertaintyReason}
        </p>
      )}

      {/* Options */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
        <div style={{
          flex: 1, minWidth: 130,
          padding: "8px 12px", borderRadius: 6,
          background: "#0f172a", border: "1px solid #1e3a5f",
        }}>
          <div style={{ fontSize: 11, fontWeight: 700, color: "#60a5fa", marginBottom: 2 }}>
            Option A : {strategy}
          </div>
          {scoreA != null && (
            <div style={{ fontSize: 10, color: "#6b7280" }}>
              Score : {scoreA.toFixed(4)}
            </div>
          )}
        </div>

        {isTie && tieAlternative && (
          <div style={{
            flex: 1, minWidth: 130,
            padding: "8px 12px", borderRadius: 6,
            background: "#0f172a", border: "1px solid #1e3a5f",
          }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: "#34d399", marginBottom: 2 }}>
              Option B : {tieAlternative}
            </div>
            {scoreB != null && (
              <div style={{ fontSize: 10, color: "#6b7280" }}>
                Score : {scoreB.toFixed(4)}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {onConfirmA && (
          <button
            onClick={() => onConfirmA(service)}
            style={{
              padding: "4px 12px", borderRadius: 6, fontSize: 11,
              fontWeight: 600, cursor: "pointer",
              background: "#1e3a5f", color: "#60a5fa",
              border: "1px solid #0284c7",
            }}
          >
            Confirmer {strategy}
          </button>
        )}
        {isTie && onChooseB && (
          <button
            onClick={() => onChooseB(service)}
            style={{
              padding: "4px 12px", borderRadius: 6, fontSize: 11,
              fontWeight: 600, cursor: "pointer",
              background: "#052e16", color: "#34d399",
              border: "1px solid #10b981",
            }}
          >
            Choisir {tieAlternative}
          </button>
        )}
        {onReject && (
          <button
            onClick={() => onReject(service)}
            style={{
              padding: "4px 12px", borderRadius: 6, fontSize: 11,
              fontWeight: 600, cursor: "pointer",
              background: "#1f2937", color: "#9ca3af",
              border: "1px solid #374151",
            }}
          >
            Rejeter
          </button>
        )}
      </div>
    </div>
  );
}
