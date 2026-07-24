# Flow-builder UI (Next.js + React Flow)

## 1. Install

```bash
npm install @xyflow/react lucide-react
```

## 2. Files

- `components/CalculationNode.jsx` — the box UI: title + two clickable
  "Column Mapping" / "Table Mapping" chips, plus a remove (X) button on
  non-main nodes. This is a **custom React Flow node**, so you can restyle
  it freely (it's just a normal React component).
- `components/FlowBuilder.jsx` — the canvas: holds nodes/edges state, wires
  up click handlers, add/remove logic, and connection drawing.
- `app/flow-builder-page-example.jsx` — copy this into
  `app/flow-builder/page.jsx`. It must be dynamically imported with
  `ssr: false` because React Flow measures the DOM and breaks under SSR.

## 3. How each interaction maps to code

| Interaction | Where it happens |
|---|---|
| Click a mapping box (Column/Table Mapping) | `onMappingClick` in `FlowBuilder.jsx` — currently logs to console; replace with opening a modal, navigating, calling an API, etc. |
| Add a new "Base View" chained to Main | `addBaseView()` — pushes a new node + a new edge in one go |
| Remove a Base View | the `X` button on the node calls `handleRemove(nodeId)`, which strips the node and any edges touching it |
| Drag to create a new connection | built into React Flow via `onConnect` — drag from the small dot (Handle) on one box to another |
| Move boxes around | built in — `onNodesChange` + `applyNodeChanges` |
| Curved connector lines like your reference image | `edgeDefaults` sets `type: "bezier"` with an orange stroke and arrowhead, matching the diagram |

## 4. Extending this

- **Multiple chained levels** (base view feeding into another base view,
  not just into main): just add more nodes/edges with different
  `source`/`target` pairs — the diagram isn't limited to a hub-and-spoke
  shape.
- **Different node types**: register more entries in `nodeTypes` in
  `FlowBuilder.jsx` (e.g. `calculationNode`, `filterNode`, `joinNode`).
- **Persisting the flow**: `nodes` and `edges` are plain JSON-serializable
  state — `JSON.stringify` them to save, and pass saved data back into
  `useState(initialNodes)` to restore.
- **Validating connections**: pass an `isValidConnection` prop to
  `<ReactFlow>` if you want to restrict which boxes can connect to which
  (e.g. prevent a Base View connecting to another Base View directly).

## 5. Why React Flow instead of hand-rolled SVG

Your reference image needs: draggable/clickable boxes, dynamically
add/remove nodes, and smooth bezier connector lines that stay attached as
boxes move. React Flow (`@xyflow/react`) already solves the hard parts —
edge routing math, hit-testing, pan/zoom, minimap/controls — so you're
only writing the box UI and your business logic, not connector geometry.
