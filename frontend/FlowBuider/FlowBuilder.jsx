"use client";

import { useCallback, useState, useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import CalculationNode from "./CalculationNode";

const MAIN_ID = "main-view";

const initialNodes = [
  {
    id: MAIN_ID,
    type: "calculationNode",
    position: { x: 40, y: 200 },
    data: { label: "Calculation Main View name", variant: "main" },
  },
  {
    id: "base-1",
    type: "calculationNode",
    position: { x: 480, y: 40 },
    data: { label: "Calculation Base View name", variant: "base" },
  },
  {
    id: "base-2",
    type: "calculationNode",
    position: { x: 480, y: 380 },
    data: { label: "Calculation Base View name", variant: "base" },
  },
];

const edgeDefaults = {
  type: "bezier",
  animated: false,
  style: { stroke: "#f5a623", strokeWidth: 1.5 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "#f5a623" },
};

const initialEdges = [
  { id: "e-base1-main", source: "base-1", target: MAIN_ID, ...edgeDefaults },
  { id: "e-base2-main", source: "base-2", target: MAIN_ID, ...edgeDefaults },
];

let idCounter = 3;

export default function FlowBuilder() {
  const [nodes, setNodes] = useState(initialNodes);
  const [edges, setEdges] = useState(initialEdges);
  const [selectedInfo, setSelectedInfo] = useState(null);

  // ---- click handling for the Column/Table Mapping sub-boxes ----
  const handleMappingClick = useCallback((nodeId, mappingType) => {
    setSelectedInfo({ nodeId, mappingType });
    // Hook your real logic here: open a modal, fetch mapping config, etc.
    console.log(`Clicked "${mappingType}" on node ${nodeId}`);
  }, []);

  // ---- remove a base node (and its edge) ----
  const handleRemove = useCallback((nodeId) => {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId));
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
  }, []);

  // inject the callbacks into every node's data so CalculationNode can call them
  const nodesWithHandlers = useMemo(
    () =>
      nodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          onMappingClick: handleMappingClick,
          onRemove: n.id === MAIN_ID ? undefined : handleRemove,
        },
      })),
    [nodes, handleMappingClick, handleRemove]
  );

  // ---- add a new "Base View" chained to the main node ----
  const addBaseView = useCallback(() => {
    const newId = `base-${idCounter++}`;
    const yOffset = 40 + nodes.length * 170;

    setNodes((nds) => [
      ...nds,
      {
        id: newId,
        type: "calculationNode",
        position: { x: 480, y: yOffset },
        data: { label: "Calculation Base View name", variant: "base" },
      },
    ]);

    setEdges((eds) => [
      ...eds,
      { id: `e-${newId}-main`, source: newId, target: MAIN_ID, ...edgeDefaults },
    ]);
  }, [nodes.length]);

  // ---- required React Flow change handlers (drag, select, etc.) ----
  const onNodesChange = useCallback(
    (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
    []
  );
  const onEdgesChange = useCallback(
    (changes) => setEdges((eds) => applyEdgeChanges(changes, eds)),
    []
  );

  // ---- let the user draw new connections by dragging from a handle ----
  const onConnect = useCallback(
    (connection) => setEdges((eds) => addEdge({ ...connection, ...edgeDefaults }, eds)),
    []
  );

  const nodeTypes = useMemo(() => ({ calculationNode: CalculationNode }), []);

  return (
    <div style={{ width: "100%", height: "100%", position: "relative" }}>
      <div
        style={{
          position: "absolute",
          top: 12,
          left: 12,
          zIndex: 10,
          display: "flex",
          gap: 8,
          alignItems: "center",
        }}
      >
        <button
          onClick={addBaseView}
          style={{
            background: "#f5a623",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            padding: "8px 14px",
            fontSize: 13,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          + Add Base View
        </button>
        {selectedInfo && (
          <span style={{ fontSize: 12, color: "#666" }}>
            Last clicked: <strong>{selectedInfo.mappingType}</strong> on{" "}
            <code>{selectedInfo.nodeId}</code>
          </span>
        )}
      </div>

      <ReactFlow
        nodes={nodesWithHandlers}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} color="#eee" />
        <Controls />
      </ReactFlow>
    </div>
  );
}
