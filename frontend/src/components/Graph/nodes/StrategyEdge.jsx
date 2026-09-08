import { memo } from "react";
import { BaseEdge, EdgeLabelRenderer, getBezierPath } from "@xyflow/react";

function StrategyEdge({
  id,
  sourceX, sourceY, sourcePosition,
  targetX, targetY, targetPosition,
  data,
  style,
  markerEnd,
}) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

  const color = data?.color ?? "#94a3b8";
  const strategy = data?.strategy ?? "";

  return (
    <>
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: color,
          strokeWidth: 2,
          strokeDasharray: ["RETIRE", "RETAIN"].includes(strategy) ? "5 4" : undefined,
        }}
      />
      {strategy && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "none",
              background: `${color}22`,
              border: `1px solid ${color}80`,
              borderRadius: 8,
              padding: "2px 8px",
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: 0.4,
              color,
              whiteSpace: "nowrap",
              fontFamily: "var(--font-sans, system-ui)",
            }}
          >
            {strategy}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export default memo(StrategyEdge);
