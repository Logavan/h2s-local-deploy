"use client"

import { useCallback, useEffect, useMemo, useState } from "react"
import {
  ReactFlow,
  Background,
  Controls,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  MarkerType,
  Position,
  type Edge,
  type Node,
  type NodeChange,
  type EdgeChange,
  type Connection,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import CalculationNode, { type CalculationNodeData, type LinkedSource } from "./CalculationNode"

export interface FlowNodeData extends CalculationNodeData {
  /** stable id used for routing actions back to the host */
  backendId?: string
  /** parent source reference for unresolved nodes */
  sourceRef?: string
  isResolved?: boolean
  /** List of HANA tables/views available for nested CV linkage */
  linkedSources?: LinkedSource[]
}

export type FlowNode = Node<FlowNodeData, "calculationNode">

export interface FlowBase {
  id: string
  label: string
  isResolved: boolean
  sourceRef?: string
  linkedSources?: LinkedSource[]
  /** Parent node id (MAIN_ID or another base id) used to draw the connecting edge */
  parentId?: string
  /** Total column-mapping rows for this artifact (from mapping info). */
  mappingCount?: number
  /** Depth in the multi-layer tree (0 = root, 1 = first nested, 2 = nested-nested, ...). */
  depth?: number
}

export interface FlowBuilderProps {
  mainLabel: string
  mainBackendId?: string
  mainLinkedSources?: LinkedSource[]
  bases: FlowBase[]
  onMappingClick: (nodeId: string, mappingType: "Column Mapping" | "Table Mapping") => void
  onRemoveBase: (nodeId: string) => void
  onAddBase: () => void
  onResolveBase: (nodeId: string) => void
  onToggleLink: (nodeId: string, sourceRef: string, enabled: boolean) => void
  height?: number
}

const MAIN_ID = "main-view"

const edgeDefaults = {
  type: "bezier",
  animated: false,
  style: { stroke: "#f5a623", strokeWidth: 1.5 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "#f5a623" },
} as const

const nextNodeId = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

export default function FlowBuilder({
  mainLabel,
  mainBackendId,
  mainLinkedSources,
  bases,
  onMappingClick,
  onRemoveBase,
  onAddBase,
  onResolveBase,
  onToggleLink,
  height = 480,
}: FlowBuilderProps) {
  const [nodes, setNodes] = useState<FlowNode[]>(() => buildInitialNodes(mainLabel, mainBackendId, mainLinkedSources, bases))
  const [edges, setEdges] = useState<Edge[]>(() => buildInitialEdges(bases, mainBackendId).filter(e => e.source !== e.target))

  // Sync local state with props when bases actually change - preserve existing positions
  const basesKey = JSON.stringify(bases)
  useEffect(() => {
    setNodes(prevNodes => {
      const newNodes = buildInitialNodes(mainLabel, mainBackendId, mainLinkedSources, bases)
      // Preserve existing node positions for nodes that still exist
      return newNodes.map(newNode => {
        const existing = prevNodes.find(p => p.id === newNode.id)
        if (existing) {
          return { ...newNode, position: existing.position }
        }
        return newNode
      })
    })
    setEdges(buildInitialEdges(bases, mainBackendId).filter(e => e.source !== e.target))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mainLabel, mainBackendId, mainLinkedSources, basesKey])

  const onNodesChange = useCallback((changes: NodeChange[]) => setNodes(nds => applyNodeChanges(changes, nds) as FlowNode[]), [])
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges(eds => applyEdgeChanges(changes, eds)), [])
  const onConnect = useCallback((connection: Connection) => {
    // Prevent self-connections
    if (connection.source === connection.target) return
    setEdges(eds => addEdge({ ...connection, ...edgeDefaults }, eds))
  }, [])

  const nodeTypes = useMemo(() => ({ calculationNode: CalculationNode }), [])

  const handleMappingClick = useCallback(
    (nodeId: string, mappingType: "Column Mapping" | "Table Mapping") => {
      onMappingClick(nodeId, mappingType)
    },
    [onMappingClick],
  )

  const handleRemove = useCallback(
    (nodeId: string) => {
      setNodes(nds => nds.filter(n => n.id !== nodeId))
      setEdges(eds => eds.filter(e => e.source !== nodeId && e.target !== nodeId))
      onRemoveBase(nodeId)
    },
    [onRemoveBase],
  )

  const handleResolve = useCallback(
    (nodeId: string) => {
      onResolveBase(nodeId)
    },
    [onResolveBase],
  )

  const handleAddBase = useCallback(() => {
    const newId = nextNodeId("base")
    setNodes(nds => {
      // Place the new base at the next free slot in depth 1 (the
      // first-level column to the right of the main node).
      const baseCount = nds.filter(n => n.id !== MAIN_ID).length
      const pos = calculateBasePosition(
        { id: newId },
        [{ id: newId }],
        new Map<string, number>(),
        new Map<number, number>([[1, baseCount]]),
      )
      const updatedNodes: FlowNode[] = [
        ...nds,
        {
          id: newId,
          type: "calculationNode",
          position: pos,
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          data: { label: "Calculation Base View name", variant: "base" as const, isResolved: false, depth: 1 },
        },
      ]
      return updatedNodes
    })
    onAddBase()
  }, [onAddBase])

  const nodesWithHandlers = useMemo<FlowNode[]>(
    () =>
      nodes.map(n => ({
        ...n,
        data: {
          ...n.data,
          onMappingClick: handleMappingClick,
          onRemove: n.id === MAIN_ID ? undefined : handleRemove,
          onResolve: n.id === MAIN_ID ? undefined : handleResolve,
          onToggleLink,
        },
      })),
    [nodes, handleMappingClick, handleRemove, handleResolve, onToggleLink],
  )

  return (
    <div
      style={{
        width: "100%",
        height,
        position: "relative",
        background: "#fafafa",
        borderRadius: 8,
        border: "1px solid #e5e5e5",
        overflow: "hidden",
      }}
      data-testid="flow-builder"
    >
      <ReactFlow
        nodes={nodesWithHandlers}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.3, maxZoom: 1.2 }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.3}
        maxZoom={1.5}
        defaultEdgeOptions={edgeDefaults}
      >
        <Background gap={20} color="#e8e8e8" />
        <Controls
          showInteractive={false}
          style={{
            background: "#ffffff",
            border: "1px solid #e0e0e0",
            borderRadius: 8,
            boxShadow: "0 2px 8px rgba(0,0,0,0.1)",
            overflow: "hidden",
          }}
        />
      </ReactFlow>
    </div>
  )
}

// Grid layout constants. NODE_WIDTH/HEIGHT must match the dimensions set
// inline in CalculationNode.tsx; COL_SPACING / ROW_SPACING add breathing
// room between nodes so edges don't overlap.
const NODE_WIDTH = 240
const NODE_HEIGHT = 200
const ROW_SPACING = 60
// Horizontal step between depth levels — gives the multi-layer tree a
// clear left-to-right visual flow so users can read the lineage at a glance.
const DEPTH_X_STEP = NODE_WIDTH + 80

/**
 * Compute a depth-aware position for a base node.
 *
 * Multi-layer trees become hard to read when all bases are dumped in a
 * single grid. We group nodes by depth (column) and stack siblings under
 * their parent so the tree's left-to-right lineage is visually obvious:
 *
 *   Root ─▶ L1 children ─▶ L2 children ─▶ L3 children ...
 *
 * Siblings are vertically stacked under the parent's row to keep edges
 * from crossing unnecessarily.
 */
function calculateBasePosition(
  base: { id: string; parentId?: string; label?: string; isResolved?: boolean },
  bases: ReadonlyArray<{ id: string; parentId?: string }>,
  parentRowOffsets: Map<string, number>,
  rowCursorByDepth: Map<number, number>,
): { x: number; y: number } {
  const depth = (() => {
    let d = 1
    let current = base.parentId
    const seen = new Set<string>()
    while (current && !seen.has(current)) {
      seen.add(current)
      d += 1
      const parent = bases.find(b => b.id === current)
      current = parent?.parentId
    }
    return d
  })()

  // Anchor each child's y to the parent's row so edges go straight down.
  let y: number
  if (base.parentId && parentRowOffsets.has(base.parentId)) {
    y = parentRowOffsets.get(base.parentId) ?? 40
  } else {
    const row = rowCursorByDepth.get(depth) ?? 0
    y = 40 + row * (NODE_HEIGHT + ROW_SPACING)
    rowCursorByDepth.set(depth, row + 1)
  }
  parentRowOffsets.set(base.id, y)

  return {
    x: 520 + (depth - 1) * DEPTH_X_STEP,
    y,
  }
}

function buildInitialNodes(mainLabel: string, mainBackendId: string | undefined, mainLinkedSources: LinkedSource[] | undefined, bases: FlowBuilderProps["bases"]): FlowNode[] {
  const mainNode: FlowNode = {
    id: MAIN_ID,
    type: "calculationNode",
    position: { x: 40, y: 200 },
    sourcePosition: Position.Right,
    targetPosition: Position.Right,
    data: {
      label: mainLabel || "Calculation Main View name",
      variant: "main",
      backendId: mainBackendId,
      isResolved: true,
      linkedSources: mainLinkedSources,
      mappingCount: bases.reduce((sum, b) => sum + (b.linkedSources?.length || 0), 0),
      depth: 0,
    },
  }
  // Compute depth from parentId chain so each node knows which layer
  // it belongs to. Defaults to 1 (direct child of main) if missing.
  const depthById = new Map<string, number>()
  for (const base of bases) {
    const parentDepth = base.parentId ? (depthById.get(base.parentId) ?? 0) : 0
    const depth = (base.parentId ? parentDepth + 1 : 1)
    depthById.set(base.id, depth)
  }
  // Position bases using the depth-aware layout. Walk bases in declared
  // order; the layout function maintains a cursor per depth so siblings
  // stack vertically without re-running an expensive layout pass.
  const parentRowOffsets = new Map<string, number>()
  const rowCursorByDepth = new Map<number, number>()
  const baseNodes: FlowNode[] = bases.map((base) => {
    const pos = calculateBasePosition(base, bases, parentRowOffsets, rowCursorByDepth)
    return {
      id: base.id,
      type: "calculationNode",
      position: pos,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        label: base.label,
        variant: "base",
        backendId: base.id,
        isResolved: base.isResolved,
        sourceRef: base.sourceRef,
        linkedSources: base.linkedSources,
        mappingCount: base.mappingCount,
        depth: base.depth ?? depthById.get(base.id) ?? 1,
      },
    }
  })
  return [mainNode, ...baseNodes]
}

// Build edges from parentId -> base.id for every base that has a parentId
function buildInitialEdges(bases: FlowBuilderProps["bases"], mainBackendId?: string): Edge[] {
  // knownIds must include BOTH the visual MAIN_ID (used by the main node in
  // buildInitialNodes) AND the main node's backend artifact ID (used as
  // base.parentId when a nested CV's parent is the root calculation view).
  // Without mainBackendId in the set, edges from the root to first-level
  // nested CVs would be silently filtered out, leaving bases disconnected.
  const knownIds = new Set<string>([MAIN_ID, mainBackendId, ...bases.map(b => b.id)].filter(Boolean) as string[])
  return bases
    .filter(base => base.parentId && knownIds.has(base.parentId) && base.parentId !== base.id)
    .map(base => ({
      id: `e-${base.parentId}-${base.id}`,
      source: base.parentId as string,
      target: base.id,
      ...edgeDefaults,
      label: base.sourceRef && base.sourceRef.length > 0 ? base.sourceRef : undefined,
      labelStyle: { fontSize: 10, fill: "#666" },
      labelBgPadding: [4, 2] as [number, number],
      labelBgBorderRadius: 3,
      labelBgStyle: { fill: "#ffffffcc", stroke: "#e5e5e5", strokeWidth: 1 },
    }))
}
