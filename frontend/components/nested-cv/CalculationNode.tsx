"use client"

import { useState } from "react"
import { Handle, Position, type NodeProps } from "@xyflow/react"
import { ChevronDown, ChevronRight, Link2, X } from "lucide-react"

export type CalculationNodeVariant = "main" | "base"

export interface LinkedSource {
  sourceRef: string
  sourceRefRaw: string
  objectKind: "physical_table" | "calculation_view" | "unknown"
  isLinked: boolean
  linkedArtifactId?: string
  linkedArtifactName?: string
}

export interface CalculationNodeData extends Record<string, unknown> {
  label: string
  variant: CalculationNodeVariant
  isResolved?: boolean
  sourceRef?: string
  linkedSources?: LinkedSource[]
  onMappingClick?: (nodeId: string, mappingType: "Column Mapping" | "Table Mapping") => void
  onRemove?: (nodeId: string) => void
  onResolve?: (nodeId: string) => void
  onToggleLink?: (nodeId: string, sourceRef: string, enabled: boolean) => void
}

const HANDLE_STYLE: React.CSSProperties = {
  background: "#f5a623",
  width: 14,
  height: 14,
  border: "3px solid #fff",
  boxShadow: "0 0 0 1px #f5a623",
  zIndex: 10,
}

export default function CalculationNode({ id, data }: NodeProps) {
  const nodeData = data as CalculationNodeData
  const isMain = nodeData.variant === "main"
  const [showLinkageDropdown, setShowLinkageDropdown] = useState(false)

  const borderColor = isMain ? "#f5a623" : "#c0c0c0"
  const headerBg = isMain ? "#fef3e2" : "#f5f5f5"

  const linkedSources = nodeData.linkedSources || []
  const linkedCount = linkedSources.filter(s => s.isLinked).length

  return (
    <div
      style={{
        border: `2px solid ${borderColor}`,
        borderRadius: 8,
        background: isMain ? "#fef3e2" : "#ffffff",
        width: 240,
        padding: "14px",
        boxShadow: "0 2px 6px rgba(0,0,0,0.1)",
        position: "relative",
        fontFamily: "system-ui, sans-serif",
        overflow: "visible",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {/* Target handle - where edges END (receiving end) - centered vertically */}
      <Handle
        type="target"
        position={isMain ? Position.Right : Position.Left}
        style={{
          ...HANDLE_STYLE,
          top: "50%",
          transform: "translateY(-50%)",
        }}
      />
      {/* Source handle - where edges START (sending end) - centered vertically */}
      <Handle
        type="source"
        position={Position.Right}
        style={{
          ...HANDLE_STYLE,
          top: "50%",
          transform: "translateY(-50%)",
        }}
      />

      {/* Remove button */}
      {nodeData.onRemove && (
        <button
          onClick={e => {
            e.stopPropagation()
            nodeData.onRemove?.(id)
          }}
          title="Remove this view from the flow"
          aria-label="Remove this view"
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            width: 22,
            height: 22,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            color: "#d32f2f",
            borderRadius: 4,
            transition: "all 0.15s",
          }}
          onMouseEnter={event => {
            event.currentTarget.style.background = "rgba(211, 47, 47, 0.1)"
          }}
          onMouseLeave={event => {
            event.currentTarget.style.background = "transparent"
          }}
        >
          <X size={14} strokeWidth={2.5} />
        </button>
      )}

      {/* Title */}
      <div
        style={{
          fontWeight: 600,
          fontSize: 13,
          color: "#1a1a1a",
          wordBreak: "break-word",
          lineHeight: 1.3,
          paddingRight: 24,
        }}
      >
        {nodeData.label || "Untitled"}
      </div>

      {/* Mapping buttons */}
      <div style={{ display: "flex", gap: 8 }}>
        <button
          disabled={!isMain && !nodeData.isResolved}
          onClick={() => nodeData.onMappingClick?.(id, "Column Mapping")}
          style={{
            flex: 1,
            border: `1.5px solid ${isMain || nodeData.isResolved ? "#f5a623" : "#d0d0d0"}`,
            borderRadius: 6,
            background: isMain || nodeData.isResolved ? "#fff8ec" : "#fafafa",
            padding: "8px 6px",
            fontSize: 11,
            fontWeight: 500,
            cursor: nodeData.isResolved || isMain ? "pointer" : "not-allowed",
            color: isMain || nodeData.isResolved ? "#222" : "#999",
            textAlign: "center",
            lineHeight: 1.3,
            opacity: !isMain && !nodeData.isResolved ? 0.5 : 1,
            transition: "all 0.15s",
          }}
          onMouseEnter={event => {
            if (nodeData.isResolved || isMain) {
              event.currentTarget.style.background = "#f5e6cc"
            }
          }}
          onMouseLeave={event => {
            event.currentTarget.style.background = isMain || nodeData.isResolved ? "#fff8ec" : "#fafafa"
          }}
        >
          Column
          <br />
          Mapping
        </button>
        <button
          disabled={!isMain && !nodeData.isResolved}
          onClick={() => setShowLinkageDropdown(!showLinkageDropdown)}
          style={{
            flex: 1,
            border: `1.5px solid ${isMain || nodeData.isResolved ? "#f5a623" : "#d0d0d0"}`,
            borderRadius: 6,
            background: isMain || nodeData.isResolved ? "#fff8ec" : "#fafafa",
            padding: "8px 6px",
            fontSize: 11,
            fontWeight: 500,
            cursor: nodeData.isResolved || isMain ? "pointer" : "not-allowed",
            color: isMain || nodeData.isResolved ? "#222" : "#999",
            textAlign: "center",
            lineHeight: 1.3,
            opacity: !isMain && !nodeData.isResolved ? 0.5 : 1,
            transition: "all 0.15s",
            position: "relative",
          }}
          onMouseEnter={event => {
            if (nodeData.isResolved || isMain) {
              event.currentTarget.style.background = "#f5e6cc"
            }
          }}
          onMouseLeave={event => {
            event.currentTarget.style.background = isMain || nodeData.isResolved ? "#fff8ec" : "#fafafa"
          }}
        >
          <Link2 size={11} style={{ display: "inline-block", marginRight: 3, verticalAlign: "middle" }} />
          Nested CV
          <br />
          Linkage {linkedCount > 0 ? `(${linkedCount})` : ""}
          {showLinkageDropdown ? (
            <ChevronDown size={10} style={{ display: "inline-block", marginLeft: 3, verticalAlign: "middle" }} />
          ) : (
            <ChevronRight size={10} style={{ display: "inline-block", marginLeft: 3, verticalAlign: "middle" }} />
          )}
        </button>
      </div>

      {/* Nested CV Linkage Dropdown */}
      {showLinkageDropdown && linkedSources.length > 0 && (
        <div
          style={{
            border: "1px solid #e0e0e0",
            borderRadius: 6,
            background: "#ffffff",
            maxHeight: 240,
            overflowY: "auto",
            marginTop: -4,
          }}
          onClick={e => e.stopPropagation()}
        >
          <div
            style={{
              padding: "8px 10px",
              fontSize: 10,
              fontWeight: 600,
              color: "#666",
              textTransform: "uppercase",
              letterSpacing: 0.5,
              borderBottom: "1px solid #f0f0f0",
              background: "#fafafa",
              position: "sticky",
              top: 0,
            }}
          >
            HANA Tables / Views
          </div>
          {linkedSources.map(source => (
            <div
              key={source.sourceRef}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 10px",
                borderBottom: "1px solid #f5f5f5",
                fontSize: 11,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1, minWidth: 0 }}>
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: 4,
                    background: source.isLinked ? "#22c55e" : "#d0d0d0",
                    flexShrink: 0,
                  }}
                  title={source.isLinked ? "Linked" : "Not linked"}
                />
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    fontFamily: "monospace",
                    color: source.isLinked ? "#15803d" : "#333",
                    fontWeight: source.isLinked ? 500 : 400,
                  }}
                  title={source.sourceRefRaw || source.sourceRef}
                >
                  {source.sourceRefRaw || source.sourceRef}
                </span>
              </div>
              {/* Toggle Switch */}
              <button
                onClick={e => {
                  e.stopPropagation()
                  nodeData.onToggleLink?.(id, source.sourceRef, !source.isLinked)
                }}
                title={source.isLinked ? "Click to unlink" : "Click to link nested CV"}
                style={{
                  width: 32,
                  height: 18,
                  borderRadius: 9,
                  border: "none",
                  background: source.isLinked ? "#22c55e" : "#d0d0d0",
                  position: "relative",
                  cursor: "pointer",
                  transition: "background 0.2s",
                  padding: 0,
                  flexShrink: 0,
                  marginLeft: 8,
                }}
                aria-pressed={source.isLinked}
                aria-label={`Toggle link for ${source.sourceRefRaw || source.sourceRef}`}
              >
                <span
                  style={{
                    position: "absolute",
                    top: 2,
                    left: source.isLinked ? 16 : 2,
                    width: 14,
                    height: 14,
                    borderRadius: 7,
                    background: "#ffffff",
                    transition: "left 0.2s",
                    boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
                  }}
                />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Empty state when dropdown open but no sources */}
      {showLinkageDropdown && linkedSources.length === 0 && (
        <div
          style={{
            border: "1px solid #e0e0e0",
            borderRadius: 6,
            background: "#fafafa",
            padding: "12px",
            fontSize: 11,
            color: "#666",
            textAlign: "center",
          }}
        >
          No HANA tables/views found in this CV.
        </div>
      )}

      {/* Resolve button for unresolved base nodes */}
      {!isMain && !nodeData.isResolved && nodeData.onResolve && (
        <button
          onClick={() => nodeData.onResolve?.(id)}
          style={{
            width: "100%",
            background: "#f5a623",
            color: "#fff",
            border: "none",
            borderRadius: 6,
            padding: "8px 10px",
            fontSize: 12,
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          + Resolve as Nested CV
        </button>
      )}
    </div>
  )
}
