import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, BackgroundVariant,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import ServiceNode from "./nodes/ServiceNode";
import NodeDetailPanel from "./NodeDetailPanel";
import { graphToRFNodes, graphToRFEdges } from "./utils/transforms";
import { dagLayout, groupedLayout } from "./utils/layout";

const nodeTypes = { service: ServiceNode };

export default function SourceGraph({ graph, artifacts, sourceCloud }) {
  const [selectedNode, setSelectedNode] = useState(null);
  const fitDone = useRef(false);

  const { rfNodes, rfEdges } = useMemo(() => {
    if (!graph) return { rfNodes: [], rfEdges: [] };
    const baseNodes = graphToRFNodes(graph, sourceCloud);
    const baseEdges = graphToRFEdges(graph);
    const laidOut = baseEdges.length > 0
      ? dagLayout(baseNodes, baseEdges)
      : groupedLayout(baseNodes, "type");
    return { rfNodes: laidOut, rfEdges: baseEdges };
  }, [graph, sourceCloud]);

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

  if (!graph) {
    return (
      <EmptyState
        icon="🌐"
        msg="No dependency data available for this migration."
      />
    );
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
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

function EmptyState({ icon, msg }) {
  return (
    <div
      style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        height: "100%", color: "var(--text-tertiary, #71717a)", fontSize: 14,
        flexDirection: "column", gap: 10,
      }}
    >
      <span style={{ fontSize: 30 }}>{icon}</span>
      <span>{msg}</span>
    </div>
  );
}
