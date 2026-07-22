# Backend — Enterprise Edition

## Overview
Enterprise edition of HANACV2SQL — a trimmed, self-contained version of the SaaS tool (`C:\Users\logav\Downloads\h2s-may29-v2`). No Supabase, no payments, no GCS. All conversions run locally with results stored in `OUTPUT_DIR`.

## Reference
For the full SaaS version (with payments, Supabase, GCS, notifications), see: `C:\Users\logav\Downloads\h2s-may29-v2\backend`

## Tech Stack
- Flask (Python 3)
- Gemini API (`gemini-3.1-flash-lite-preview` / `gemini-2.5-flash-lite`) for AI SQL enhancement
- Local filesystem storage for output files

## Key Files
```
flask_app.py              # Main Flask app — USE THIS
bulk_processor.py         # Bulk ZIP file analysis and processing
file_processor.py         # XML to SQL conversion logic
node_counter.py           # Node counting and validation
api_client.py             # Gemini API client
mapping_sql_generator.py  # SQL generation from column mapping
sql_converter.py          # Core XML → SQL conversion
local_storage.py          # Local filesystem output storage
node_cache.py             # Node dict caching
excel_encrypt.py          # Excel decryption utilities
requirements.txt          # Python dependencies
config/settings.py        # Environment variable config
.env                      # API keys and OUTPUT_DIR (not committed)
```

## API Routes

### Single File Conversion
```
POST /api/analyze                          # Analyze XML, count nodes, validate structure
POST /api/start-conversion                 # Start conversion task, returns task_id
GET  /api/conversion-status/<task_id>     # Poll conversion status
GET  /api/download/<session_id>           # Download converted file (sql or mapping); falls back to local disk
GET  /api/previous-conversions            # List mapping files from previous conversions
POST /api/validate                         # Validate XML structure only
```

### Bulk Conversion
```
POST /api/bulk-analyze                    # Analyze ZIP file, count all nodes
POST /api/bulk-conversion                 # Start bulk conversion, returns bulk_task_id
GET  /api/bulk-status/<bulk_task_id>     # Poll bulk conversion status
GET  /api/bulk-download/<bulk_task_id>   # Download all converted files as ZIP
```

### Mapping Engine
```
POST /api/mapping/upload_and_generate_schema        # Upload mapping XLSX, generate schema
POST /api/mapping/apply_changes_and_generate_output # Apply edits, generate output SQL
```

### Utility
```
GET  /                          # Root — lists all endpoints
GET  /health                    # Health check
GET  /api/status                # Backend status
POST /container-shutdown         # Cloud Run shutdown handler
GET  /debug-latency             # Latency debug endpoint
```

## Bulk Processing Flow

### 1. Bulk Analyze (`POST /api/bulk-analyze`)
```
Input: multipart/form-data with ZIP file
Process:
  1. Extract all XML/TXT files from ZIP
  2. For each file: count nodes, determine complexity
  3. Return summary

Response:
{
  success: true,
  files: [{ file_name, node_count, complexity, id }],
  total_files: 50,
  total_nodes: 234
}
```

### 2. Start Bulk Conversion (`POST /api/bulk-conversion`)
```
Input: JSON with files array
Process:
  1. Queue each file for conversion
  2. Return immediately with bulk_task_id

Response:
{
  success: true,
  bulk_task_id: "uuid",
  message: "Bulk conversion started for 50 files"
}
```

### 3. Poll Bulk Status (`GET /api/bulk-status/<bulk_task_id>`)
```
Response:
{
  status: "PROCESSING" | "COMPLETED" | "PARTIAL" | "FAILED",
  progress: 75,
  total_files: 50,
  completed_files: 37,
  failed_files: 2,
  results: [{ file_name, status, sql_content?, error? }, ...],
  message: "37 completed, 2 failed, 11 processing"
}
```

### 4. Download Bulk Results (`GET /api/bulk-download/<bulk_task_id>`)
```
Response: ZIP file download containing all converted SQL files
Filename: "bulk_converted_<bulk_task_id>.zip"
```

## Storage

### Task Results
- Stored **in-memory** in `conversion_tasks` dict (single file) and `bulk_tasks` dict (bulk)
- File bytes (`sql_content`, `mapping_content`) held in memory after conversion
- On download: served directly from memory — no second fetch needed

### Output Files
- `local_storage.py` saves conversion outputs to `OUTPUT_DIR` on disk
- `OUTPUT_DIR` defaults to `C:\Users\logav\Downloads\Output_h2s_local` (set in `.env`)
- Structure: `<OUTPUT_DIR>/<task_id>/<cv_object_name>.zip`

## AI Enhancement

### Gemini Configuration
- `GEMINI_API_KEY` in `backend/.env` — key from [Google AI Studio](https://aistudio.google.com/app/apikey)
- **Primary model**: `gemini-3.1-flash-lite-preview` via `genai.Client` SDK (used for refinement passes)
- **Secondary model**: `gemini-2.5-flash-lite` via httpx REST (used for main SQL generation)
- Both paths tested and working — see `backend/test_gemini.py`

### Model Paths
| Call | Model | Transport |
|------|-------|-----------|
| `api_call('Gemini', ...)` | `gemini-2.5-flash-lite` | httpx REST |
| `api_call('gemini-3.1-flash-lite-preview', ...)` | `gemini-3.1-flash-lite-preview` | `genai.Client` SDK |
| `api_call_flash('Gemini', ...)` | `gemini-2.5-flash-lite` | httpx REST (alias) |

## Environment Variables

**`backend/.env`** — all sensitive config lives here (never committed):
```
GEMINI_API_KEY=                    # Required — Google AI Studio key
OUTPUT_DIR=C:\Users\logav\Downloads\Output_h2s_local   # Converted file output
PREVIOUS_CONVERSIONS_DIR=          # Optional — dir for "Select from History" feature; defaults to OUTPUT_DIR
```

**Do not set `SUPABASE_*` vars** — Supabase is not used in this edition.

## Run Locally
```bash
cd backend
pip install -r requirements.txt
python flask_app.py       # Development ( waitress on :8080, debug mode on)
# or
python run.py             # Production mode with waitress
```

## Deployment
Dockerfile provided. Set env vars at runtime (not baked into image). Cloud Run compatible.

## Logging
Uses Python `logging` module. Local dev: `DEBUG` level. Cloud Run: `INFO` level.

## Testing
- Test script: `backend/test_gemini.py` — validates Gemini API key and both model paths
- **Do not** place test files inside `backend/` — use `visual-testing/` at project root
