# Nested CV Flattener — Enhanced UI Plan

## Context

User's core idea: The Nested CV Flattener needs a **right-click context menu** on source tables/views in the tree. Right-clicking a table/view should expose:
1. **"Adjust Column & Table Mapping"** → opens MappingEditorPopup with that node's SQL/mappings
2. **"Nested Calculation View"** → opens the NestedDependencyModal to resolve that table/view as a nested CV using a previous conversion's XLSX

Additionally:
- An **"Enable Nested CV"** button/toggle per tree node to mark it as a nested resolution candidate
- The dependency chain continues recursively until the user clicks **"Enable Nested CV"** to finalize

---

## Goal

Transform the current flat "Add CV → tree appears" UX into an **interactive dependency builder** where:
1. User starts a session and adds a root CV (from XLSX or History)
2. The tree shows all source tables/views used by that CV
3. **Right-click any table/view** in the tree → context menu
4. "Adjust Column & Table Mapping" → opens MappingEditorPopup for inline mapping edits
5. "Nested Calculation View" → resolves that table as a nested CV using a previous conversion XLSX (mapping its columns to the parent CV's columns)
6. Repeat until all dependencies resolved → Generate

---

## Current UI (Baseline)

From `NestedCVTool.tsx`:

- **Top**: Platform selector (6 platforms) + Start Session button
- **Left panel (320px)**: Collapsible tree — ROOT + children, kind badges (CV/TBL/VIEW), + button per node (opens NestedDependencyModal), × remove button, checkbox
- **Right panel (70%)**: Detail form — Table/View name input, Columns tag list (add/remove), SQL preview (readonly), Description textarea, Save button
- **Bottom**: Generate bar → result editor + download
- **Modals**: NestedDependencyModal, ConversionHistoryModal, MappingEditorPopup

Current issues:
- `nestedAddCvFromXlsx` called without `parentSourceRef` — backend ignores parent context
- No right-click menu on source tables
- No "Adjust Column & Table Mapping" entry point
- No per-node "Enable Nested CV" toggle
- Detail panel is read-only for SQL — can't edit mappings inline

---

## Enhanced UI Specification

### 1. Tree Node Structure (Left Panel)

Each tree node represents an artifact in the dependency graph:

```
ROOT (CV) — the root Calculation View
├── source: SOURCES (table) — physical table, no nested CV
│   └── [source nodes have right-click menu]
├── source: SCALMONTH (table) — physical table
│   └── source: <nested CV node> — resolved via XLSX
│       └── [nesting continues recursively]
└── ...
```

**Node types:**
| Kind | Badge | Can have children? | Right-click menu? |
|------|-------|---------------------|-------------------|
| `calculation_view` | CV (purple) | Yes — shows its own sources as children | Yes: Adjust Mapping, Nested CV, Remove |
| `physical_table` | TBL (blue) | No | Yes: Adjust Mapping, Nested CV, Remove |
| `nested_resolved` | NST (green) | Yes — shows nested CV's sources | Yes: Adjust Mapping, Remove |

**Right-click context menu options per node:**
1. ✏️ **"Adjust Column & Table Mapping"** — opens MappingEditorPopup for this node's SQL + mappings
2. ➕ **"Nested Calculation View"** — opens NestedDependencyModal pre-filled with this node's `source_ref_canonical` as the parent to resolve
3. 🗑️ **"Remove"** — removes node from tree

**Visual:**
- Right-click anywhere on the node row (excluding the + and × buttons)
- Small `⋮` (kebab) icon visible on hover, right side of node row
- Clicking it opens the context menu below/above the node

### 2. Context Menu Component

**File:** `frontend/components/nested-cv/NodeContextMenu.tsx` (new)

```
Position: absolute, anchored to node row
Width: 220px
Background: white, border-radius 8px, shadow-xl
Z-index: 100+

Menu items:
┌─────────────────────────────────┐
│ ✏ Adjust Column & Table Mapping │  ← opens MappingEditorPopup
├─────────────────────────────────┤
│ ➕ Nested Calculation View       │  ← opens NestedDependencyModal
├─────────────────────────────────┤
│ 🗑️ Remove                        │  ← removes from tree
└─────────────────────────────────┘
```

- Clicking outside closes the menu
- `Escape` key closes the menu
- Menu renders via Portal to avoid overflow:hidden clipping
- Each menu item has icon + label, hover state (gray-50 bg)

### 3. "Enable Nested CV" Toggle

**Location:** In the detail panel (right side) when a node is selected, above the columns list

```
┌──────────────────────────────────────────────────┐
│  Table / View Name                                │
│  [V_SCALMONTH________________________________]    │
├──────────────────────────────────────────────────┤
│  Kind: TBL  │  Status: Physical Table            │
├──────────────────────────────────────────────────┤
│  ☑ Enable Nested CV    ← checkbox/toggle         │
│  (Mark this table as a nested calculation view   │
│   that will be resolved from a previous CV)      │
├──────────────────────────────────────────────────┤
│  Columns:                                          │
│  [calmonth ×] [datafl ×] [+]                      │
└──────────────────────────────────────────────────┘
```

**Behavior:**
- Checkbox visible for all `physical_table` nodes
- When checked: node kind changes to `nested_candidate`, badge turns green, + button becomes prominent
- When unchecked: reverts to `physical_table`
- Does NOT automatically open modal — user must right-click → "Nested Calculation View" to trigger resolution

### 4. Detail Panel Enhancements (Right Panel)

**Name input:** editable, syncs to tree node name
**Kind badge:** shows current kind (CV/TBL/NST)
**Status line:** "Physical Table" | "Nested Candidate" | "Resolved Nested CV"
**"Enable Nested CV" checkbox:** (per node, see above)
**Columns tag list:** editable — add/remove columns that this CV/table exposes
**SQL Preview (readonly):** shows the actual SQL for this node
**"Adjust Mappings" button:** shortcut to open MappingEditorPopup (same as right-click → menu item)
**Description textarea:** editable description

### 5. Right-Click → "Adjust Column & Table Mapping"

**Flow:**
1. User right-clicks node → "Adjust Column & Table Mapping"
2. Context menu closes
3. `MappingEditorPopup` opens with:
   - `platformName` = current session's `target_dialect`
   - `sqlContent` = node's SQL (from `sqlContent` field)
   - `zipContents` = node's mapping info (table list + mapping fields)
   - `fileName` = node name
4. User edits mappings → clicks Save in popup
5. Popup closes → node's mapping data updated in tree state
6. Updated mappings sent to backend via `nestedUpdateMappings`

**Implementation note:**
- The node needs `mapping_info` and `sql_info` stored alongside `sqlContent`
- `nestedAddCvFromXlsx` response includes `sql_info` and `mapping_info` — these must be stored on the TreeNode
- Currently only `sql_chunks` (SQL string) is stored; need to also store the structured mapping data

**TreeNode interface updated:**
```typescript
interface TreeNode {
  id: string
  name: string
  kind: "calculation_view" | "physical_table" | "nested_resolved" | "nested_candidate"
  sqlContent?: string        // SQL for this node
  sqlInfoRows?: SqlInfoRow[] // structured sql_info from XLSX (for mapping)
  mappingInfoRows?: Record<string, string>[] // mapping_info from XLSX
  mappingFieldsDict?: Record<string, string[]> // { sourceTable: [col1, col2] }
  columns: string[]
  description?: string
  children: TreeNode[]
  parentId: string | null
  enableNestedCv: boolean    // toggled by user
  artifactId?: string        // backend artifact ID (if uploaded)
}
```

### 6. Right-Click → "Nested Calculation View"

**Flow:**
1. User right-clicks node → "Nested Calculation View"
2. Context menu closes
3. **NEW**: `NestedDependencyModal` opens pre-filled:
   - `parentRef` = node's `source_ref_canonical` (e.g., "SCALMONTH")
   - `parentName` = node name (e.g., "V_SCALMONTH")
   - Modal shows Upload tab + History tab
   - User uploads XLSX of a previous CV that represents this table/view
   - Backend parses XLSX → finds matching source by name → creates child artifact
4. Child node added to tree under the parent node
5. Child node's kind = `nested_resolved`, badge = green "NST"

**Key change — `nestedAddCvFromXlsx` call:**
```typescript
// Pass parentSourceRef so backend knows this is a child resolution
const res = await nestedAddCvFromXlsx(
  sessionId,
  file,
  selectedSource,       // the source in the XLSX that maps to parentRef
  art.artifact_id        // pass parent artifact ID
)
```

**Backend change required:**
- `POST /api/nested/sessions/<id>/cvs/xlsx` must accept `parentSourceRef` and `parentArtifactId` parameters
- When provided, create the artifact and link it as a child of the parent artifact
- This requires the backend endpoint to handle the `parentSourceRef` and `parentArtifactId` parameters (currently ignored)

### 7. Backend Changes Required

**File:** `backend/flask_app.py` — `POST /api/nested/sessions/<id>/cvs/xlsx`

Add two optional form fields:
- `parentSourceRef` (string): the `source_ref_canonical` from the parent artifact
- `parentArtifactId` (string): the artifact ID of the parent CV

When both are provided:
1. Parse the uploaded XLSX as usual
2. Find the source in the XLSX that matches `parentSourceRef`
3. Create the child artifact
4. Call `resolve_links` to create a `DependencyLink`:
   - `consumer_artifact_id` = parent artifact ID
   - `source_ref_canonical` = `parentSourceRef`
   - `resolution` = `"uploaded_cv"`
   - `producer_artifact_id` = new child artifact ID

**File:** `backend/nested_cv/tasks.py` — `_run_generation`

When `len(artifacts) == 1` path uses `generate_sql_from_mapping`. This path must work for:
1. Single root CV with `physical_table` sources (no nested) → just generate SQL
2. Single root CV with some `nested_resolved` sources → those nested artifacts are included in the session but their SQL is inlined via `generate_sql_from_mapping` which handles the CTE chain

### 8. MappingEditorPopup Integration

**File:** `frontend/components/MappingEditorPopup.tsx` (existing — do NOT modify)

**Current props:**
```typescript
interface MappingEditorPopupProps {
  isOpen: boolean
  onClose: () => void
  platformName: string
  sqlContent: string
  zipContents: {
    sqlFiles: Record<string, string>
    mappingFileContent: Record<string, string>[]
    textFileName: string
  }
  onSave: (mappings: MappingChange[]) => void
}
```

**How it's used in MappingTool.tsx:**
```typescript
<MappingEditorPopup
  isOpen={showMappingEditor}
  onClose={() => setShowMappingEditor(false)}
  platformName={platform.name}
  sqlContent={currentMappingSql}
  zipContents={{ sqlFiles: {}, mappingFileContent: currentMappings, textFileName: currentFileName }}
  onSave={async (mappings) => {
    // apply to backend
  }}
/>
```

**Integration in NestedCVTool:**
```typescript
function handleAdjustMappings(nodeId: string) {
  const node = tree.flat().find(n => n.id === nodeId)
  if (!node) return
  setMappingNodeId(nodeId)
  setMappingSql(node.sqlContent || "")
  setMappingFileName(node.name)
  setMappingRows(node.mappingInfoRows || [])
  setShowMappingEditor(true)
}

// In the popup state:
const [mappingNodeId, setMappingNodeId] = useState<string | null>(null)
const [mappingSql, setMappingSql] = useState("")
const [mappingRows, setMappingRows] = useState<Record<string, string>[]>([])

<MappingEditorPopup
  isOpen={showMappingEditor}
  onClose={() => setShowMappingEditor(false)}
  platformName={targetDialect}
  sqlContent={mappingSql}
  zipContents={{ sqlFiles: {}, mappingFileContent: mappingRows, textFileName: mappingFileName }}
  onSave={async (mappings) => {
    // Persist to backend via nestedUpdateMappings
    if (mappingNodeId && sessionId) {
      const node = tree.flat().find(n => n.id === mappingNodeId)
      // Convert MappingChange[] to backend MappingEntry[] format
      const entries: MappingEntry[] = mappings.map(m => ({
        source_ref_canonical: m.sourceTable,
        source_column_raw: m.sourceColumn,
        target_table: m.targetTable,
        target_column: m.targetColumn,
        artifact_id: node?.artifactId,
      }))
      await nestedUpdateMappings(sessionId, entries)
      // Update local state
      updateNode(mappingNodeId, { mappingInfoRows: mappings })
    }
    setShowMappingEditor(false)
  }}
/>
```

### 9. State Updates

**New state variables in NestedCVTool.tsx:**
```typescript
// Context menu
const [contextMenu, setContextMenu] = useState<{
  nodeId: string
  x: number
  y: number
} | null>(null)

// Mapping editor
const [mappingNodeId, setMappingNodeId] = useState<string | null>(null)
const [mappingSql, setMappingSql] = useState("")
const [mappingFileName, setMappingFileName] = useState("")
const [mappingRows, setMappingRows] = useState<Record<string, string>[]>([])

// Nested modal (pre-filled from right-click)
const [pendingNestedParent, setPendingNestedParent] = useState<{
  nodeId: string
  sourceRef: string
  nodeName: string
} | null>(null)
```

**TreeNode `kind` type expanded:**
```typescript
type NodeKind = "calculation_view" | "physical_table" | "nested_resolved" | "nested_candidate"
```

### 10. File Structure

```
frontend/
├── components/
│   └── nested-cv/
│       ├── NestedCVTool.tsx          ← ENHANCED: add context menu, enable nested toggle
│       ├── NestedDependencyModal.tsx ← ENHANCED: accept pre-filled parentRef + parentArtifactId
│       ├── NodeContextMenu.tsx       ← NEW: right-click context menu component
│       └── ContextMenuPortal.tsx     ← NEW: portal for context menu to escape overflow:hidden
```

---

## Implementation Steps

### Step 1: Create NodeContextMenu component
- Absolute positioned menu anchored to click coordinates
- Three menu items with icons and handlers
- Portal rendering
- Click-outside and Escape to close

### Step 2: Create ContextMenuPortal component
- Simple portal to `document.body`
- Renders children (the context menu) outside the tree panel

### Step 3: Update TreeNode interface and state
- Add `kind: NodeKind`, `sqlInfoRows`, `mappingInfoRows`, `enableNestedCv`, `artifactId` fields
- Update `addRootNode`, `addChildNode`, `updateNode` functions

### Step 4: Add kebab menu icon to tree nodes
- Add `⋮` icon (3-dot/kebab) visible on hover, right side of node row
- On click: open context menu at cursor position

### Step 5: Implement context menu handlers
- `handleAdjustMappings(nodeId)` → sets mapping state + opens MappingEditorPopup
- `handleNestedCv(nodeId)` → sets pendingNestedParent + opens NestedDependencyModal
- `handleRemoveNode(nodeId)` → calls `removeNode`

### Step 6: Enhance detail panel (right side)
- Add kind badge (CV/TBL/NST)
- Add status line
- Add "Enable Nested CV" checkbox
- Add "Adjust Mappings" button (shortcut)
- Keep SQL preview readonly

### Step 7: Update NestedDependencyModal
- Accept optional `initialParentRef` and `initialParentArtifactId` props
- When provided, auto-fill `parentRef` and `parentName`
- In Upload tab, after parsing XLSX → auto-match the source that corresponds to `initialParentRef`
- When user confirms, pass both file + selected source + `initialParentArtifactId` to `handleNestedConfirm`

### Step 8: Update `handleNestedConfirm` to pass parent info
- Call `nestedAddCvFromXlsx(sessionId, file, selectedSource, parentArtifactId)` with parent info
- Backend must then link child → parent via `resolve_links`

### Step 9: Backend update — `POST /api/nested/sessions/<id>/cvs/xlsx`
- Accept optional `parentSourceRef` and `parentArtifactId` in form data
- When both provided: after parsing, create a DependencyLink from parent → child

### Step 10: Update `nestedAddCvFromXlsx` in api.ts
- Add optional `parentSourceRef?: string` and `parentArtifactId?: string` parameters
- Pass as form fields to backend

### Step 11: Test end-to-end flow
1. Start session with BigQuery
2. Add root CV from History
3. Tree shows root CV + its source tables
4. Right-click a source table → "Adjust Column & Table Mapping" → edit in MappingEditorPopup → Save
5. Right-click a source table → "Nested Calculation View" → upload previous CV XLSX → Confirm
6. Child appears under the source table node
7. Repeat for all dependencies
8. Click Generate → flat SQL in editor
9. Download

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Context menu trigger | Kebab `⋮` icon on hover (not raw right-click) | Avoids conflicts with text selection and OS-level context menu |
| "Adjust Mapping" opens MappingEditorPopup | Yes | Reuses existing component, user already knows this UI |
| "Enable Nested CV" is a checkbox, not auto-trigger | Yes | Gives user control to mark candidates before resolving |
| Recursive nesting | Yes | Nested CVs can have their own nested dependencies |
| Backend links child to parent on upload | Yes | Keeps session consistent; no separate "resolve" step needed |
| `generate_sql_from_mapping` for single CV | Yes | Byte-for-byte identical with MappingTool output |
| Multi-artifact path | Yes | `compose_sql` from `sql_composer.py` for full flattening |

---

## Out of Scope (for this enhancement)

- Changes to `MappingEditorPopup.tsx` component itself
- Changes to `ConversionHistoryModal.tsx`
- Changes to backend `mapping_sql_generator.py`
- Changes to `NestedSessionStore`, `DependencyGraph`, `SqlComposer`, `PySparkComposer`
- The "Enable Nested CV" auto-resolution (user manually triggers via right-click menu)

---

## Acceptance Criteria

1. ✅ Right-clicking any node shows context menu with 3 options
2. ✅ "Adjust Column & Table Mapping" opens MappingEditorPopup with that node's SQL + mappings
3. ✅ Saving mappings in popup persists to backend via `nestedUpdateMappings`
4. ✅ "Nested Calculation View" opens NestedDependencyModal pre-filled with parent info
5. ✅ Confirming modal adds child node under the parent node in tree
6. ✅ "Remove" removes node from tree (and calls backend delete if artifactId exists)
7. ✅ "Enable Nested CV" checkbox toggles node state without triggering modal
8. ✅ Detail panel shows kind badge, status, columns, SQL preview, "Adjust Mappings" button
9. ✅ Generate works for both single-artifact (parity path) and multi-artifact (flattening path)
10. ✅ UI matches reference image layout: tree left, detail right, generate bottom
