# HANACV2SQL — Enterprise Edition

## Overview

Self-contained enterprise deployment of the SAP HANA Calculation View → SQL conversion tool.
A trimmed fork of the SaaS version (`C:\Users\logav\Downloads\h2s-may29-v2`) with **no Supabase, no GCS, no payments, no credits**. All conversions run locally.

## Three Tools

### Tool 1: HANA CV Converter (`ConversionTool.tsx`)
Converts SAP HANA Calculation View XML files to platform-specific SQL.

- **Single mode**: Upload one XML → analyze → convert → download ZIP
- **Bulk mode**: Upload ZIP of multiple XMLs → analyze all → convert all → download all as ZIP
- Output saved to `OUTPUT_DIR/<task_id>/<cv_object_name>.zip`
- Mapping sheet saved to `OUTPUT_DIR/<task_id>/<cv_object_name>_mapping_sheet.xlsx`
- Results persist on disk forever (never auto-deleted)

### Tool 2: SQL/PySpark Mapping Engine (`MappingTool.tsx`)
Edit column mappings from a converter-generated XLSX, generate platform-specific SQL.

- Upload XLSX → edit table/column mappings in popup → generate SQL or PySpark
- Platforms: BigQuery, Snowflake, Databricks, Fabric, Redshift, Datasphere
- Results shown in code editor directly (in-memory, no disk)
- Download as `.sql` or `.ipynb` from the editor

### Tool 3: Nested CV Flattener (`NestedCVTool.tsx`)
Merge multiple nested HANA CV JSON definitions into one flat SQL/PySpark output.

- Paste/load multiple CV JSON files → resolve dependencies → validate graph → generate merged output
- Platforms: BigQuery, Snowflake, Databricks, Fabric, Redshift, Datasphere
- Formats: SQL or PySpark DataFrame API
- Results shown in code editor directly (in-memory, no disk)
- Download as `.sql` or `.pyspark` from the editor

## Tech Stack

| Layer | Stack |
|-------|-------|
| Frontend | Next.js 16 App Router, TypeScript strict, Tailwind CSS, Framer Motion, shadcn/ui, CodeMirror |
| Backend | Flask + waitress, Python 3.11 |
| AI | Gemini API — single model configurable via `GEMINI_MODEL` env var |
| Storage | Local filesystem (`OUTPUT_DIR`) + in-memory for active sessions |
| BigQuery | `google-cloud-bigquery` for SQL validation against GCP |

## Project Structure

```
h2s-local-deploy/
├── backend/
│   ├── flask_app.py              # Main Flask app — USE THIS (single source of truth)
│   ├── run.py                   # Production runner (waitress, optional)
│   ├── file_processor.py         # Core XML→SQL conversion logic
│   ├── sql_converter.py          # ZIP packaging and formatting
│   ├── bulk_processor.py         # ThreadPool bulk ZIP processing
│   ├── node_counter.py           # XML validation and node counting
│   ├── api_client.py             # Gemini API client (httpx + genai SDK)
│   ├── mapping_sql_generator.py  # SQL generation from column mapping XLSX
│   ├── local_storage.py          # OUTPUT_DIR file storage
│   ├── node_cache.py            # Pickle cache for node dictionaries
│   ├── excel_encrypt.py         # AES Fernet XLSX encryption
│   ├── bq_table.py              # BigQuery table operations (used by file_processor)
│   ├── bq_error_fixer.py        # BigQuery error fixing via Gemini
│   ├── nested_cv/               # Nested CV Flattener module
│   │   ├── models.py            # NestedSession, CvArtifact, DependencyLink, NestedTask
│   │   ├── session_store.py     # Thread-safe in-memory session/task store
│   │   ├── artifact_parser.py    # CV JSON mapping workbook parser
│   │   ├── dependency_graph.py  # DAG construction, topological sort
│   │   ├── mapping_service.py   # Mapping deduplication and conflict detection
│   │   ├── tasks.py             # Async generation task lifecycle (single → mapping_sql_generator; multi → orchestrator)
│   │   └── orchestrator.py      # Multi-artifact chaining (CTE / HANA table-function / PySpark)
│   ├── config/
│   │   └── settings.py          # Environment variable config (GEMINI_API_KEY required)
│   └── requirements.txt         # Python dependencies
│
├── frontend/
│   ├── app/                    # Next.js App Router pages
│   ├── components/
│   │   ├── ConversionTool.tsx   # HANA CV Converter (single + bulk)
│   │   ├── MappingTool.tsx      # SQL/PySpark Mapping Engine
│   │   ├── nested-cv/
│   │   │   └── NestedCVTool.tsx # Nested CV Flattener
│   │   ├── TabSwitcher.tsx     # Tool tab switcher
│   │   ├── GraphvizViewer.tsx  # HANA dependency graph SVG
│   │   └── ui/                 # shadcn/ui components
│   ├── lib/
│   │   ├── api.ts              # All backend API calls
│   │   ├── config.ts           # NEXT_PUBLIC_API_BASE_URL
│   │   └── nested-cv-types.ts  # Nested CV TypeScript types
│   └── contexts/
│       └── EnterpriseContext.tsx # No-auth enterprise context
│
├── data/outputs/               # Default OUTPUT_DIR for local dev
├── visual-testing/             # Playwright E2E tests (NOT inside frontend/backend)
├── docker-compose.enterprise.yml
└── backend/Dockerfile.enterprise
```

## Run Locally

```bash
# Backend
cd backend
pip install -r requirements.txt
python flask_app.py       # Runs on http://localhost:8080

# Frontend (separate terminal)
cd frontend
npm install               # or pnpm install
npm run dev               # Runs on http://localhost:3000
```

## Run with Docker

```bash
docker-compose -f docker-compose.enterprise.yml up --build
# Frontend: http://localhost:3000
# Backend:  http://localhost:8080
# Health:   http://localhost:8080/health
```

## Environment Variables

### Backend (`backend/.env`)

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `GEMINI_API_KEY` | **Yes** | — | Google AI Studio key. App raises `ValueError` at startup if missing. |
| `H2S_HMAC_KEY` | No | ephemeral random | 32-byte base64 HMAC signing key. Stable across restarts only when set. |
| `H2S_ALLOWED_ORIGINS` | Conditional | `*` in dev only | Comma-separated browser origins. Required in production — app refuses to start otherwise. |
| `OUTPUT_DIR` | No | `<backend>/../data/outputs` | Where conversion ZIPs are stored permanently |
| `PREVIOUS_CONVERSIONS_DIR` | No | Falls back to `OUTPUT_DIR` | Separate dir for "Select from History" |

### Frontend (`frontend/.env.local`)

| Variable | Value |
|----------|-------|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8080` |

## Licensing

Every deployment — local or production — needs a vendor-signed `license.json` bound to the host's machine fingerprint. There is no dev-mode bypass or skip flag. The repo ships `laptop-license.json` at the root bound to the original developer's laptop; for any other host (your second laptop, a customer's VM), get a new license signed by the vendor. See `backend/licensing/claude.md` for the full flow.

## Output Storage

Conversion outputs (ZIP + mapping sheet) are saved to `OUTPUT_DIR` **permanently**. No automatic cleanup. Structure:

```
<OUTPUT_DIR>/
└── <task_id>/
    ├── <cv_object_name>.zip                    ← Converted SQL files
    └── <cv_object_name>_mapping_sheet.xlsx    ← Column mapping reference
```

`OUTPUT_DIR` is **exclusively for HANA CV Converter** results. NestedCVTool and MappingTool results are held in-memory and shown in the browser editor — they never touch `OUTPUT_DIR`.

## Key Differences from SaaS Version

| Feature | SaaS (h2s-may29-v2) | Enterprise (this) |
|---------|---------------------|-------------------|
| Auth | Supabase email/password | None — always authenticated |
| Payments | PhonePe, PayPal, credits | None — unlimited |
| Storage | GCS + Supabase | Local filesystem + memory |
| DB | Supabase PostgreSQL | None |
| AI models | Gemini, DeepSeek | Gemini only |

## SaaS Reference

Full SaaS version with payments, Supabase, GCS, and notifications:
`C:\Users\logav\Downloads\h2s-may29-v2`
