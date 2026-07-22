# Frontend

## Tech Stack
- Next.js 16 App Router, TypeScript strict mode
- Tailwind CSS for styling
- Server Actions for mutations

## Key Files
```
lib/
  api.ts                # Backend API calls helper (fetchWithTimeout, etc.)
  config.ts             # Environment config
  conversions.ts        # Conversion utilities

contexts/
  AuthContext.tsx        # Auth state management

app/
  actions/              # Server actions
    conversion-actions.ts        # XML/SQL conversion
    newsletter-actions.ts      # Newsletter signup
  tools/
    hana-converter/     # Main conversion page
    sql-mapping/         # Column mapping page

components/
  Header.tsx              # Navigation bar
  ConversionTool.tsx      # Main conversion UI (SINGLE + BULK)
  FileUploadArea.tsx      # Drag & drop file upload
  GraphvizViewer.tsx      # HANA dependency graph (SVG)
  account/
    ConversionHistory.tsx
  conversion/
    FileUploadSection.tsx
    BulkFileUploadSection.tsx  # ZIP file upload for bulk
    ConversionStatus.tsx
  ui/                     # shadcn/ui components
```

## Conversion Modes

### Single File Mode
- Upload one XML file
- Immediate analysis and conversion
- Progress via polling

### Bulk Mode
- Upload ZIP file with multiple XML/TXT files
- Analyze all files at once
- Real-time Conversion Summary Dashboard
- Download all results as single ZIP

## API Functions (lib/api.ts)

### Single File
```typescript
analyzeXmlFile(xmlContent, fileName, userEmail)
// Returns: { success, node_count, complexity, conversion_type, session_id }

startConversion(xmlContent, fileName, userEmail, conversionType, nodeCount)
// Returns: { success, task_id, message }

getConversionStatus(taskId)
// Returns: { status, progress, message, result?: { sql_url, data_mapping_url } }

downloadConvertedFile(sessionId, fileName)
// Triggers browser download
```

### Bulk Operations
```typescript
analyzeBulkZip(zipFile, userEmail)
// Returns: { success, files[], total_nodes }

startBulkConversion(files, userEmail)
// Returns: { success, bulk_task_id, message }
// Timeout: 120 seconds

getBulkConversionStatus(taskId)
// Returns: { status, progress, total_files, completed_files, failed_files, results[] }

downloadBulkResult(taskId)
// Triggers browser download with timestamped filename
```

### Utility
```typescript
checkBackendHealth()
// Returns: { status: "alive" | "down" }

checkConversionRunningStatus(userEmail)
// Returns: { isRunning: boolean }
```

## Conversion Summary Dashboard

Shown during bulk conversion polling:

```
┌─────────────────────────────────────┐
│ Conversion Summary                  │
├─────────────────────────────────────┤
│ Total Files    │ 50                 │
│ Processing     │ 11                 │
│ Completed      │ 37  ✅             │
│ Failed         │ 2   ❌             │
├─────────────────────────────────────┤
│ ████████████████████░░░░░ 78%      │
├─────────────────────────────────────┤
│ file1.xml ........... Done          │
│ file2.xml ........... Processing    │
│ file3.xml ........... Failed        │
│   → Invalid XML structure           │
│ ...                                │
└─────────────────────────────────────┘
```

## Error Handling

### Frontend Error Filter
Only validation-like errors pass through. Internal errors show generic message:

**Passed through:**
- "Root View:ColumnView element not found"
- "No XML content provided"
- "Invalid XML structure"

**Filtered (generic shown):**
- Python tracebacks
- MemoryError, RecursionError
- `[WinError ...]`, socket errors
- Stack traces

### Timeout Configuration
| Operation | Timeout |
|-----------|---------|
| Health check | 10s |
| Single analyze/convert | 30s |
| Bulk analyze | 120s |
| Bulk start conversion | 120s |
| Status polling | 5s interval, 720 attempts (60 min max) |

## Key Components

### ConversionTool.tsx
Main component handling both single and bulk modes:

**State:**
- `conversionMode: "single" | "bulk"`
- `processingState: "idle" | "analyzing" | "checking-limits" | "initiating-conversion" | "polling-status" | "success" | "error"`
- `bulkFiles: BulkFileInfo[]`
- `bulkTaskId: string | null`
- `bulkProgress: { completed, total, failed }`

**Key Functions:**
- `handleProcessClick()` - Single file conversion
- `handleStartBulkConversion()` - Bulk conversion start
- `pollConversionStatus()` - Poll single file status
- `pollBulkConversionStatus()` - Poll bulk status
- `handleDownload()` - Download (single or bulk based on mode)

## Environment Variables
```
NEXT_PUBLIC_API_BASE_URL      # Backend API (http://localhost:8080 locally)
```

## Testing
- **DO NOT** place test files inside `frontend/` or `backend/` folders
- All frontend/backend testing goes in `visual-testing/` at project root
- This avoids Docker build issues (test files shouldn't be in Docker context)
- Use `visual-testing/tests/bulk-conversion.spec.ts` for Playwright E2E tests
