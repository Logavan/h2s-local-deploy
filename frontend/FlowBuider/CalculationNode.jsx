"use client";

import { Handle, Position } from "@xyflow/react";
import { X } from "lucide-react";

/**
 * Custom node: a titled box with two clickable "mapping" chips inside
 * (Column Mapping / Table Mapping), matching the reference diagram.
 *
 * node.data shape:
 * {
 *   label: string,
 *   variant: "main" | "base",
 *   onMappingClick: (nodeId, mappingType) => void,
 *   onRemove: (nodeId) => void,   // undefined/omitted for the main node
 * }
 */
export default function CalculationNode({ id, data }) {
  const isMain = data.variant === "main";

  const borderColor = isMain ? "#f5a623" : "#d0d0d0";
  const bg = "#ffffff";

  return (
    <div
      style={{
        border: `2px solid ${borderColor}`,
        borderRadius: 6,
        background: bg,
        padding: "16px 18px",
        minWidth: 220,
        boxShadow: "0 1px 3px rgba(0,0,0,0.08)",
        position: "relative",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      {/* connection handles so edges can attach left/right */}
      <Handle type="target" position={Position.Left} style={{ background: "#f5a623" }} />
      <Handle type="source" position={Position.Right} style={{ background: "#f5a623" }} />

      {/* remove button for non-main nodes */}
      {data.onRemove && (
        <button
          onClick={() => data.onRemove(id)}
          title="Remove this view"
          style={{
            position: "absolute",
            top: 6,
            right: 6,
            width: 20,
            height: 20,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            color: "#999",
            borderRadius: 4,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "#f2f2f2")}
          onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
        >
          <X size={14} />
        </button>
      )}

      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12, color: "#222" }}>
        {data.label}
      </div>

      <div style={{ display: "flex", gap: 10 }}>
        {["Column Mapping", "Table Mapping"].map((mapping) => (
          <button
            key={mapping}
            onClick={() => data.onMappingClick?.(id, mapping)}
            style={{
              border: "1.5px solid #f5a623",
              borderRadius: 4,
              background: "#fff",
              padding: "10px 14px",
              fontSize: 13,
              cursor: "pointer",
              color: "#333",
              textAlign: "center",
              lineHeight: 1.3,
              transition: "background 0.15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "#fff8ec")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "#fff")}
          >
            {mapping.split(" ")[0]}
            <br />
            {mapping.split(" ")[1]}
          </button>
        ))}
      </div>
    </div>
  );
}
