# Nested CV Flattener — Frontend

> **Living document.** This file MUST be kept in sync whenever code in this folder
> changes. If you add/rename a component, change a flow, fix a bug, or alter an
> API contract, update the relevant section here. The next session will rely on
> this to understand the folder without re-deriving everything.

---

## Purpose

The third tool in the HANACV2SQL app (`?tab=nested`). Merges multiple nested
HANA Calculation View JSON definitions into one flat SQL/PySpark output. Targets:
BigQuery, Snowflake, Databricks, Microsoft Fabric, Redshift, SAP Datasphere.

---

## Two Distinct Concepts (do NOT conflate)

This is the #1 source of confusion. The folder has two parallel systems:

### 1. Per-CV Column Mapping *(editable, accessed via right-click)*
- **Scope:** per-CV (parent or nested — no distinction)
- **Source:** the `mapping info` sheet of the Excel uploaded for that CV
- **Behavior:** display + edit. One editable grid per CV. Rows are
  `source_table / source_field / target_table / target_field`.
- **Entry point:** right-click any node → "Adjust Column & Table Mapping" → opens
  `MappingEditorPopup`. Table-level renames are a SEPARATE modal
  (`TableMappingModal`) — different purpose.
- **For nested vs base sources:** the editor treats both the same — no
  special-case UI per row type.

### 2. Per-toggle Nested CV Lineage *(read-only join at upload time)*
- **Trigger:** user toggles a source inside a node's "Nested CV Linkage" dropdown.
- **Action:** opens `NestedDependencyModal` to upload a nested CV (Excel file).
- **Join semantics:**
  - **Left side** = mapping info columns for that specific table/view, taken
    from the **parent CV's** mapping info
  - **Right side** = SELECT aliases from the **last chunk's SQL** of the
    newly uploaded nested CV's mapping info (extracted server-side by
    `_inspect_xlsx_workbook`)
- **Result:** a recorded linkage between parent source_ref ↔ nested CV artifact.
- **The auto-match UI is `NestedColumnMappingModal`** — opened AFTER upload to
  let the user confirm/edit the auto-built join.

### Multi-layered recursion (same logic at every layer)

```
Root CV ──has──▶ Source A ──toggle/upload──▶ Nested CV 1
                       Source B ──toggle/upload──▶ Nested CV 2

Nested CV 1 ──has──▶ Source X ──toggle/upload──▶ Nested-Nested CV 1a
                                Source Y ──toggle/upload──▶ Nested-Nested CV 1b
```

A nested CV at layer 2 is itself a CV with its own mapping info + its own
source toggles. Its sources can be toggled → upload more nested CVs at layer 3.
**Same pattern at every depth:** column mapping + nested lineage, identical flow.

---

## File Inventory

| File | Role |
|------|------|
| `NestedCVTool.tsx` | Main container. Holds all session state, builds the FlowBuilder, dispatches API calls. The right-side detail/edit panel lives here. |
| `FlowBuilder.tsx` | ReactFlow canvas with `CalculationNode`s, parent→child edges, depth-aware layout. Pure rendering — handlers come from `NestedCVTool` via props. |
| `CalculationNode.tsx` | Single ReactFlow node. Shows title, mapping-count pill (only when > 0), layer badge (only when depth > 0), two action buttons (Column Mapping, Nested CV Linkage), and the linkage dropdown with Linked/Base badges. Right-click → `NodeContextMenu`. |
| `NestedDependencyModal.tsx` | Upload modal with two tabs (Upload New / Select from History). History tab is a clean list with a Refresh button — NO internal-path debug text shown to users. After upload, calls `onUploadXlsx` (inspect-only) and populates column-mapping state for `NestedColumnMappingModal`. |
| `NestedColumnMappingModal.tsx` | Auto-built join editor. LEFT = parent required columns; RIGHT = nested CV output columns (last-chunk aliases). Tier 1 exact match, tier 2 token overlap (length-weighted). |
| `TableMappingModal.tsx` | Source-table → target-table renames. Output-side only. Separate from column mapping. |
| `NodeContextMenu.tsx` | Right-click menu with three actions: Adjust Column & Table Mapping, Nested Calculation View, Remove. Disabled states per node kind. |

---

## State Architecture (NestedCVTool.tsx)

```
session ────────────► NestedSession from /api/nested/sessions/<id>
tree ──────────────► TreeNode[] built from session via buildTree()
selectedNode ──────► derived from tree + selectedNodeId
expandedIds ───────► Set<string> for tree view expansion
localColumns ─────► Record<nodeId, string[]> user-edited column lists
descriptions ──────► Record<nodeId, string> user-edited descriptions
```

Critical invariants:
- `mainLinkedSources` is memoized via `useMemo([session, tree])` — do NOT
  recompute inside the FlowBuilder IIFE or ReactFlow's effect over-fires.
- The `pollGenerationRef` cancels generation. Bump it before any await in
  `handleGenerate` so prior in-flight loops exit cleanly.
- `lastSelectedIdRef` guards the selected-node effect from clobbering
  unsaved edits when `localColumns` / `descriptions` change.
- `NestedDependencyModal` uses `lastModalContextRef` keyed on
  `isOpen|mode|parentRef` so opening a new dependency context wipes the
  upload state. `isEditingSourceRef` is part of that reset.

---

## Data Flow

### Upload a root CV
1. User picks platform/format → `createSession()` → POST /api/nested/sessions
2. User clicks "Add First CV" → `openRootUpload()` → `NestedDependencyModal`
3. Upload Excel → backend inspects (no artifact created yet) → modal populates
4. User clicks "Add Root CV" → `handleNestedConfirm(file, source, mappings)`
5. Backend POSTs to /cvs/xlsx with `inspectOnly=false` → creates artifact
6. Session refresh → node appears in FlowBuilder

### Link a source to a nested CV
1. User clicks "Nested CV Linkage" dropdown on a node → sees parent's sources
2. User toggles a Base source → `setPendingNestedParent({consumerArtifactId,
   sourceRef})` → opens `NestedDependencyModal` in nested mode
3. Modal fetches `parentRequiredColumns` from the parent artifact's dependency
4. User uploads nested CV's Excel → backend extracts output_columns (last-chunk
   aliases) and mapping info
5. Modal auto-maps parent ↔ nested via `autoBuildColumnMappings` (exact, then
   token overlap with length-weighted score)
6. User reviews/edits the join → confirms
7. Backend POSTs to /cvs/xlsx with `parentArtifactId` → creates nested artifact
   + adds `DependencyLink(consumer=parent, source_ref, resolution=uploaded_cv,
   producer=new_artifact)`

### Edit column mappings
1. Right-click any node → "Adjust Column & Table Mapping"
2. `openMappingEditor(node)` builds rows from `node.mappings`
3. `MappingEditorPopup` opens in-place (reused from MappingTool)
4. On save → `nestedUpdateMappings` with merged full mapping list

### Remove a node
1. Right-click → "Remove" (or `handleRemove` from FlowBuilder)
2. `nestedDeleteCv(sessionId, artifactId)` → cascades server-side
3. Selected node cleared if it referenced the removed artifact

---

## Key Conventions

### Naming
- File `claude.md` (lowercase) is the per-folder CLAUDE.md for Claude sessions.
- `nested-cv` (kebab-case) is the folder name; `NestedCVTool.tsx` etc. are PascalCase.
- Artifact IDs are UUIDs. Source refs are uppercased canonical strings.
- Tree node IDs use `source:<artifactId>:<encodedSourceRef>` for source nodes,
  bare `artifactId` for artifact nodes. Use the encoding when constructing, decode
  mentally when matching.

### LinkedSource construction
For a **nested CV base** in the FlowBuilder, mappings are stored under the
PARENT's artifact_id scoped by `source_ref_canonical`. `buildLinkedSourcesFor`
takes optional `parentArtifactId` + `parentSourceRef` to find them correctly.
Forgetting this returns `[]` and the linkage badge reads "0".

### Column mappings for a nested CV — where to look
There are TWO sets of mappings for a nested CV base node:

1. **Join mappings** — `global_mappings` where `artifact_id = parent_artifact_id`
   AND `source_ref_canonical = baseMatch.sourceRef`. These are created by the
   toggle→upload→join flow when the user confirms the auto-built column mapping.
   If the parent didn't declare required columns, this set may be EMPTY.
2. **Own mapping info rows** — `global_mappings` where `artifact_id = baseMatch.id`,
   plus `session.artifacts[baseMatch.id].mapping_rows`. These come from the
   nested CV's own uploaded Excel.

`handleMapping` loads BOTH sets (`[...joinMappings, ...ownMappings]`) and
`openMappingEditor` falls back to `artifact.mapping_rows` if BOTH are empty.
Without this fallback, a nested CV whose parent didn't declare required
columns shows "No column mappings are available" even though the artifact
itself has mapping info.

### Mapping-info sheet column names
The XLSX `mapping info` sheet uses Excel-native column names:
`Original Table / Original Column / New Table / New Column`. The backend
**renames these to `sourceTable / sourceField / targetTable / targetField`**
inside `_inspect_xlsx_workbook` BEFORE serializing, so both the inspect
endpoint and the upload endpoint see the same shape. **Do not remove this
rename** — without it the upload handler's
`row.get('sourceTable', '')` reads empty for every row and the
`if src_table and src_col` filter drops all mapping rows silently. The
user sees "No column mappings are available" even though their Excel file
has data.

### Deriving parent's required columns from mapping_rows
When the user toggles a source (e.g. SCALMONTH) and opens the "Resolve
Nested Dependency" modal, the modal needs the parent's required columns
for that source to drive auto-match. The backend populates these from the
workbook's `SourceTable_mapping_fields` column (a stringified dict). If
that field is missing/malformed OR doesn't list the toggled source, the
backend leaves `required_columns_json=[]` for that dependency — and the
modal falls back to "Parent CV did not declare required columns…".

The frontend fix (NestedCVTool.tsx, ~line 1497) is: when the explicit
list is empty, derive from `parentArtifact.mapping_rows` filtered by
`source_ref_canonical === sourceUpper`, deduped case-insensitively,
extracting `source_column_raw`. This works because the `mapping info`
sheet's source-side columns ARE the columns the parent uses from each
source — the `SourceTable_mapping_fields` is just a more explicit
declaration of the same thing.

Why not fix this on the backend? The backend fix would be to populate
`required_columns_json` from `mapping_rows` at upload time. But the
fallback at upload would only fire for the parent's own dependencies, and
later when the user toggles, the parent's already-saved
`required_columns_json` is what gets read. Better to fix at read time
when the toggled source matches.

### Depth-aware layout
Nodes are positioned by depth (column) with siblings stacked under their
parent's row. The depth is computed from `parentId` chains. Don't fall back to
flat grids — multi-layer trees become unreadable.

### Modal z-index hierarchy
- NestedDependencyModal: `z-[70]`
- MappingEditorPopup: `z-[60]` (inherits via portal)
- TableMappingModal: `z-50`
- NestedColumnMappingModal: `z-[80]`
- Fullscreen result editor: `z-[60]` BUT auto-disables when ANY modal is open
  so the user can interact with the modal normally.

---

## API Contracts (frontend-side expectations)

```typescript
POST   /api/nested/sessions                        { target_dialect, output_format }
GET    /api/nested/sessions/<id>                   → NestedSession
DELETE /api/nested/sessions/<id>                   → ok
POST   /api/nested/sessions/<id>/cvs/xlsx          multipart: xlsxFile,
                                                    parentSourceRef?, parentArtifactId?,
                                                    inspectOnly?, selectedSource?
                                                    → inspect payload OR {artifact, session}
PATCH  /api/nested/sessions/<id>/cvs/<artifactId>  { cv_display_name, ... }
DELETE /api/nested/sessions/<id>/cvs/<artifactId>
PUT    /api/nested/sessions/<id>/mappings          { mappings: MappingEntry[] }
POST   /api/nested/sessions/<id>/validate          → { valid, errors, warnings }
POST   /api/nested/sessions/<id>/generate          → { task_id }
GET    /api/nested/tasks/<taskId>                  → { status, progress, message,
                                                       result_content?, output_format? }
GET    /api/nested/tasks/<taskId>/download?filename=...   (filename is sanitized
                                                            server-side via
                                                            secure_filename)
DELETE /api/nested/tasks/<taskId>                  → cancel running task
POST   /api/nested/tasks/<taskId>/cancel           → alias for DELETE
GET    /api/nested/previous_conversions/<id>/inspect      → inspect payload
                                                            (no re-upload)
```

---

## Maintenance Checklist

When you change code in this folder, update this file:

- [ ] New component? → add row to File Inventory
- [ ] Renamed/removed component? → update File Inventory AND any cross-references
- [ ] New state field in NestedCVTool? → add to State Architecture
- [ ] New API endpoint or contract change? → add to API Contracts
- [ ] New flow or changed existing flow? → update Data Flow
- [ ] Found a non-obvious gotcha? → add to Key Conventions
- [ ] Fixed a bug whose fix isn't obvious? → add a short note explaining WHY
      so the next session doesn't accidentally re-break it
