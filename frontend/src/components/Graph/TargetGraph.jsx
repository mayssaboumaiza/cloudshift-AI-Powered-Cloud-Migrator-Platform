import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import ServiceNode from "./nodes/ServiceNode";
import NodeDetailPanel from "./NodeDetailPanel";
import { planToTargetRF, CLOUD_COLORS } from "./utils/transforms";
import { dagLayout, groupedLayout } from "./utils/layout";

const nodeTypes = { service: ServiceNode };

export default function TargetGraph({ plan, graph, artifacts, targetCloud }) {
  const [selectedNode, setSelectedNode] = useState(null);
  const fitDone = useRef(false);

  const { rfNodes, rfEdges } = useMemo(() => {
    if (!plan) return { rfNodes: [], rfEdges: [] };
    const { targetNodes, targetEdges } = planToTargetRF(plan, graph, targetCloud);
    const laidOut = targetEdges.length > 0
      ? dagLayout(targetNodes, targetEdges)
      : groupedLayout(targetNodes, "type");
    return { rfNodes: laidOut, rfEdges: targetEdges };
  }, [plan, graph, targetCloud]);

  const [nodes, setNodes, onNodesChange] = useNodesState(rfNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(rfEdges);

  useEffect(() => { setNodes(rfNodes); }, [rfNodes, setNodes]);
  useEffect(() => { setEdges(rfEdges); }, [rfEdges, setEdges]);

  const onInit = useCallback((rf) => {
    if (!fitDone.current && rfNodes.length > 0) {
      fitDone.current = true;
      requestAnimationFrame(() => rf.fitView({ padding: 0.14, duration: 350 }));
    }
  }, [rfNodes.length]);

  const onNodeClick = useCallback((_, node) => {
    setSelectedNode((prev) => (prev?.id === node.id ? null : node));
  }, []);

  const onPaneClick = useCallback(() => setSelectedNode(null), []);

  if (!plan) {
    return (
      <div
        style={{
          display: "flex", alignItems: "center", justifyContent: "center",
          height: "100%", color: "var(--text-tertiary, #71717a)", fontSize: 14,
          flexDirection: "column", gap: 10,
        }}
      >
        <span style={{ fontSize: 30 }}>🎯</span>
        <span>Target architecture not yet available — run the migration plan first.</span>
      </div>
    );
  }

  const cloudColor = CLOUD_COLORS[(targetCloud ?? "").toLowerCase()] ?? "#94a3b8";

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      {/* Target cloud badge overlay */}
      <div
        style={{
          position: "absolute", top: 12, left: 16, zIndex: 5,
          fontSize: 11, fontWeight: 700, padding: "3px 10px",
          borderRadius: 10,
          background: `${cloudColor}15`,
          border: `1px solid ${cloudColor}50`,
          color: cloudColor,
          pointerEvents: "none",
          letterSpacing: 0.3,
        }}
      >
        ☁ {(targetCloud ?? "Target").toUpperCase()} — Target Architecture
      </div>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onInit={onInit}
        nodeTypes={nodeTypes}
        minZoom={0.2}
        maxZoom={2.5}
        style={{ background: "var(--graph-bg, var(--surface-1))" }}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          color="var(--graph-grid, var(--surface-4))"
          variant={BackgroundVariant.Dots}
          gap={24}
          size={1.5}
        />
        <Controls showInteractive={false} />
        <MiniMap
          nodeColor={(n) => n.data?.color ?? "#94a3b8"}
          maskColor="rgba(0,0,0,0.05)"
          style={{
            background: "var(--surface-0, #fff)",
            border: "1px solid var(--surface-3, #eef1f7)",
            borderRadius: 8,
          }}
        />
      </ReactFlow>

      {selectedNode && (
        <NodeDetailPanel
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
          artifacts={artifacts}
        />
      )}
    </div>
  );
}
