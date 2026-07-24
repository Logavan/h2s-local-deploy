# Frontend — Enterprise Edition

## Tech Stack
- **Next.js 16** App Router, TypeScript strict mode
- **Tailwind CSS** + **Framer Motion**
- **shadcn/ui** components + **Radix UI** primitives
- **CodeMirror** — SQL/PySpark code editor
- **D3.js** — Graphviz DOT graph rendering
- **JSZip** — client-side ZIP handling for bulk downloads

## Pages

| Route | File | Description |
|-------|------|-------------|
| `/` | `app/page.tsx` | Home — renders one of three tools via `TabSwitcher` |
| `/how-to-use` | `app/how-to-use/page.tsx` | Static guide with FAQ schema |

## Three Tools (Tab-Switched on Home Page)

| Tab | Component | Purpose |
|-----|-----------|---------|
| `converter` | `ConversionTool.tsx` | HANA CV Converter — XML → SQL (single + bulk) |
| `mapper` | `MappingTool.tsx` | SQL/PySpark Mapping Engine — XLSX → SQL |
| `nested` | `NestedCVTool.tsx` | Nested CV Flattener — multi-CV → merged SQL/PySpark |

Switch via URL param: `?tab=mapper` or `?tab=nested`.

## Key Components

### HANA CV Converter (`ConversionTool.tsx`)
- `conversion/FileUploadSection.tsx` — XML drag-and-drop upload
- `conversion/BulkFileUploadSection.tsx` — ZIP upload + JSZip extraction
- `conversion/ConversionDashboard.tsx` — bulk progress table
- `conversion/ConversionSteps.tsx` — step indicator
- `conversion/SuccessState.tsx` — single file success screen
- `conversion/VisualizationSection.tsx` — Graphviz SVG + auto-download
- `GraphvizViewer.tsx` — animated HANA dependency graph

### SQL/PySpark Mapping Engine (`MappingTool.tsx`)
- `MappingEditorPopup.tsx` — modal for editing table/column mappings
- `ConversionHistoryModal.tsx` — browse previous conversions
- `CodeEditor.tsx` — CodeMirror SQL editor
- `NotebookRenderer.tsx` — `.ipynb` PySpark notebook renderer

### Nested CV Flattener (`NestedCVTool.tsx`)
- Platform selector (BigQuery, Snowflake, Databricks, Fabric, Redshift, Datasphere)
- Format selector (SQL or PySpark)
- CV JSON paste/drop area
- Dependency graph validation
- CodeMirror editor for generated output

## API Functions (`lib/api.ts`)

### HANA CV Converter

```typescript
analyzeXmlFile(xmlContent, fileName, userEmail)
// POST /api/analyze → { node_count, complexity, session_id, ... }

startConversion(xmlContent, fileName, userEmail, nodeCount?)
// POST /api/start-conversion → { task_id }

getConversionStatus(taskId)
// GET /api/conversion-status/{taskId} → { status, progress, message, result? }

downloadConvertedFile(sessionId, fileName)
// GET /api/download/{sessionId}?type=sql → browser download

analyzeBulkZip(zipFile, userEmail)
// POST /api/bulk-analyze → { files[], total_nodes }

startBulkConversion(files, userEmail)
// POST /api/bulk-conversion → { bulk_task_id }

getBulkConversionStatus(bulkTaskId)
// GET /api/bulk-status/{bulkTaskId} → { status, progress, completed_files, failed_files, results[] }

downloadBulkResult(bulkTaskId)
// GET /api/bulk-download/{bulkTaskId} → browser download

listPreviousConversions()
// GET /api/previous-conversions → { files[] }
```

### Mapping Engine

```typescript
processXlsxFileForMapping(xlsxFile, platform)
// POST /api/mapping/upload_and_generate_schema → { mappingSchema }

applyMappingChanges(updatedMappings, fileName, platform, sessionId, format?)
// POST /api/mapping/apply_changes_and_generate_output
// → { cteSqlContent, tempTableSqlContent, pysparkNotebookContent, fileName }
```

### Nested CV Flattener

```typescript
nestedCreateSession({ target_dialect, output_format })     // POST /api/nested/sessions
nestedGetSession(sessionId)                               // GET /api/nested/sessions/{id}
nestedDeleteSession(sessionId)                             // DELETE /api/nested/sessions/{id}
nestedAddCv(sessionId, { file_content, file_name })       // POST /api/nested/sessions/{id}/cvs
nestedUpdateCv(sessionId, artifactId, { emission_mode, target_view_name })  // PATCH
nestedDeleteCv(sessionId, artifactId)                     // DELETE
nestedResolveLinks(sessionId, links)                       // PUT /nested/sessions/{id}/links
nestedUpdateMappings(sessionId, mappings)                  // PUT /nested/sessions/{id}/mappings
nestedValidate(sessionId)                                 // POST /nested/sessions/{id}/validate
nestedGenerate(sessionId)                                 // POST /nested/sessions/{id}/generate
nestedGetTaskStatus(taskId)                              // GET /api/nested/tasks/{taskId}
nestedDownloadResult(taskId)                              // GET /api/nested/tasks/{taskId}/download
```

## State Management

- **EnterpriseContext** (`contexts/EnterpriseContext.tsx`) — always authenticated, no auth UI
- All tools use local `useState`/`useCallback` — no Redux/Zustand
- `ENTERPRISE_EMAIL = "enterprise@local.deploy"` hardcoded for all API calls

## Environment Variables

```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8080   # set in frontend/.env.local
```

Next.js rewrites `/api/:path*` → `${NEXT_PUBLIC_API_BASE_URL}/api/:path*`.

## Testing
- **Do NOT** place test files inside `frontend/` or `backend/` — they break Docker builds
- All Playwright E2E tests go in `visual-testing/` at project root
- `visual-testing/nested-cv/test_nested_cv_api.py` — Nested CV API tests

## Error Handling

Only user-friendly errors pass through. Internal tracebacks are filtered:

**Passed through:**
- "Root View:ColumnView element not found"
- "No XML content provided"
- "Invalid XML structure"

**Filtered (generic shown):**
- Python tracebacks, `RecursionError`, `MemoryError`
- `[WinError ...]`, socket errors, stack traces

## Key Libraries

| File | Purpose |
|------|---------|
| `lib/api.ts` | All backend API calls |
| `lib/config.ts` | `NEXT_PUBLIC_API_BASE_URL` with fallback |
| `lib/nested-cv-types.ts` | All TypeScript types for NestedCVTool |
| `hooks/useLineCounter.ts` | Web Worker-based XML line counter |
| `lib/analytics.ts` | GA4 event tracking |
