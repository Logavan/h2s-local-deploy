# Backend — Enterprise Edition

## Overview

Enterprise edition backend. Single Flask app (`flask_app.py`) handles all routes.
No Supabase, no GCS, no payments. All conversions run locally with results stored in `OUTPUT_DIR`.

## Tech Stack
- **Flask** + **waitress** (production WSGI server)
- **Gemini API** — single model configured via `GEMINI_MODEL` env var
- **BigQuery** (`google-cloud-bigquery`) — SQL validation against GCP when credentials available
- **Local filesystem** — `OUTPUT_DIR` for HANA CV Converter output only

## Key Files

```
flask_app.py              # Main Flask app — USE THIS
run.py                    # Production runner with waitress (optional)
file_processor.py         # Core XML → SQL conversion logic (~10K lines)
sql_converter.py          # ZIP packaging and SQL formatting
bulk_processor.py         # ThreadPoolExecutor bulk ZIP processing
node_counter.py           # XML validation and node counting
api_client.py             # Gemini API client (httpx + genai SDK)
mapping_sql_generator.py  # SQL generation from column mapping XLSX
local_storage.py          # OUTPUT_DIR file storage (HANA CV Converter only)
node_cache.py            # Pickle cache for node dictionaries (temp)
excel_encrypt.py          # AES Fernet XLSX decryption
bq_table.py               # BigQuery table operations (used by file_processor)
bq_error_fixer.py        # BigQuery error fixing via Gemini
nested_cv/               # Nested CV Flattener module
  models.py               # NestedSession, CvArtifact, DependencyLink, NestedTask
  session_store.py        # Thread-safe in-memory session/task store
  artifact_parser.py      # CV JSON mapping workbook parser
  dependency_graph.py     # DAG construction, topological sort, cycle detection
  mapping_service.py      # Mapping deduplication and conflict detection
  tasks.py                # Async generation task lifecycle (single → mapping_sql_generator; multi → orchestrator)
  orchestrator.py         # Multi-artifact chaining (CTE / HANA table-function / PySpark)
config/settings.py        # Environment variable config (GEMINI_API_KEY required)
requirements.txt          # Python dependencies
Dockerfile.enterprise
.env                     # API keys, OUTPUT_DIR (not committed)
```

## Complete API Routes

### Root & Health
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Service info, version, endpoint list |
| GET | `/health` | Health check |
| GET | `/api/status` | Backend alive status |
| POST | `/container-shutdown` | Cloud Run shutdown handler |

### Single File Conversion
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze` | Analyze XML, count nodes, validate structure |
| POST | `/api/start-conversion` | Start long-running conversion, returns task_id |
| GET | `/api/conversion-status/<task_id>` | Poll conversion status |
| GET | `/api/download/<session_id>` | Download converted ZIP or mapping sheet |
| GET | `/api/previous-conversions` | List mapping files from previous conversions |
| POST | `/api/validate` | Validate XML structure only |

### Bulk Conversion
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/bulk-analyze` | Analyze ZIP, count nodes across all files |
| POST | `/api/bulk-conversion` | Start bulk conversion, returns bulk_task_id |
| GET | `/api/bulk-status/<bulk_task_id>` | Poll bulk conversion progress |
| GET | `/api/bulk-download/<bulk_task_id>` | Download all converted files as ZIP |

### Mapping Engine
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/mapping/upload_and_generate_schema` | Upload XLSX, generate initial mapping schema |
| POST | `/api/mapping/apply_changes_and_generate_output` | Apply edits, generate output SQL or PySpark notebook |

### Nested CV Flattener
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/nested/sessions` | Create a new nested CV session |
| GET | `/api/nested/sessions/<session_id>` | Get session details |
| DELETE | `/api/nested/sessions/<session_id>` | Delete a session |
| POST | `/api/nested/sessions/<session_id>/cvs` | Add a CV artifact to session |
| PATCH | `/api/nested/sessions/<session_id>/cvs/<artifact_id>` | Update CV (emission_mode, target_view_name) |
| DELETE | `/api/nested/sessions/<session_id>/cvs/<artifact_id>` | Remove CV from session |
| PUT | `/api/nested/sessions/<session_id>/links` | Save dependency link resolutions |
| PUT | `/api/nested/sessions/<session_id>/mappings` | Save unified column mappings |
| POST | `/api/nested/sessions/<session_id>/validate` | Validate graph + mappings |
| POST | `/api/nested/sessions/<session_id>/generate` | Start SQL/PySpark generation task |
| GET | `/api/nested/tasks/<task_id>` | Get task status |
| GET | `/api/nested/tasks/<task_id>/download` | Download generated SQL/PySpark |

### Utility
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/debug-latency` | Gemini API latency benchmarking |

## Output Storage

### HANA CV Converter
- `local_storage.save_result()` writes to `OUTPUT_DIR/<cv_object_name>_<timestamp>/`
- Structure: `<OUTPUT_DIR>/<cv_object_name>_<timestamp>/<cv_object_name>.zip` + `<cv_object_name>.xlsx` + `_manifest.json`
- **Results persist forever — no automatic cleanup**
- Lookups by `task_id` work via `_manifest.json` scan (backwards compatible)

### NestedCVTool & MappingTool
- Results held in-memory in `NestedSessionStore` / `mapping_sessions` dict
- Shown directly in browser code editor (in-memory, never written to disk)
- Download via `nestedDownloadResult()` / blob from memory
- **OUTPUT_DIR is NOT used**

## NestedCVTool Architecture

- `NestedSessionStore` — thread-safe in-memory store. Sessions have 24h TTL; tasks have 1h TTL.
- `_run_generation()` background thread composes SQL/PySpark and stores in `task.result_content`
- Download endpoint streams from `task.result_content` via `io.BytesIO` — no disk involved
- `task.output_format` field distinguishes SQL vs PySpark for correct file extension

### Session Lifecycle
```
POST /nested/sessions → session_id
  → POST /nested/sessions/<id>/cvs (add CVs)
  → PUT /nested/sessions/<id>/links (resolve dependencies)
  → PUT /nested/sessions/<id>/mappings (set column mappings)
  → POST /nested/sessions/<id>/validate (validate graph)
  → POST /nested/sessions/<id>/generate → task_id
  → GET /nested/tasks/<task_id> (poll until COMPLETED)
  → GET /nested/tasks/<task_id>/download (download in-memory result)
```

## AI Enhancement

| Call | Model | Transport |
|------|-------|-----------|
| `api_call('Gemini', ...)` | `GEMINI_MODEL` env var | httpx REST |
| `api_call('gemini-2.5-flash-lite', ...)` | `GEMINI_MODEL` env var | `genai.Client` SDK |
| `api_call_flash('Gemini', ...)` | `GEMINI_MODEL` env var | httpx REST (alias) |

Both paths tested — see `test_gemini.py`.

## Environment Variables

**`backend/.env`** — all sensitive config (never committed):
```
GEMINI_API_KEY=                    # Required — Google AI Studio key (raises error if missing)
GEMINI_MODEL=gemini-2.5-flash-lite  # Single model for all Gemini calls (REST + SDK)
OUTPUT_DIR=C:\Users\logav\Downloads\Output_h2s_local
PREVIOUS_CONVERSIONS_DIR=         # Optional — defaults to OUTPUT_DIR
```

## Run Locally
```bash
cd backend
pip install -r requirements.txt
python flask_app.py       # waitress on :8080, debug mode on
```

## Deployment
`backend/Dockerfile.enterprise` — Python 3.11-slim multi-stage build.
Set env vars at runtime (not baked into image). Cloud Run compatible.
