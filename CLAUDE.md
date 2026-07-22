# HANACV2SQL — Enterprise Edition

## Overview
Enterprise edition of the SAP HANA Calculation View → SQL conversion tool. A trimmed, self-contained fork of the SaaS version (`C:\Users\logav\Downloads\h2s-may29-v2`). No Supabase, no payments, no GCS. All conversions run locally with results served directly from memory.

## Tech Stack

### Frontend (Next.js 16 App Router)
- **Framework**: Next.js 16 with TypeScript
- **Styling**: Tailwind CSS
- **State**: React Context (EnterpriseContext — unlimited, no auth)
- **Deployment**: Cloud Run (standalone Docker)

### Backend (Flask/Python)
- **Framework**: Flask + waitress
- **AI**: Gemini API (`gemini-3.1-flash-lite-preview`, `gemini-2.5-flash-lite`)
- **Storage**: Local filesystem (`OUTPUT_DIR`)
- **DB**: None — all state in-memory

## Project Structure
```
frontend/              # Next.js 16 app (app router)
  app/               # Pages, layouts, server actions
  components/        # React components
    ui/              # shadcn/ui components
    conversion/      # File upload, conversion status, dashboard
  lib/               # API client, config, analytics
  hooks/             # Custom React hooks
  contexts/         # EnterpriseContext (no auth)
  styles/            # Tailwind globals
  public/            # Static assets

backend/              # Flask API
  flask_app.py       # Main Flask app — USE THIS
  bulk_processor.py  # Bulk ZIP analysis and processing
  file_processor.py  # XML to SQL conversion logic
  node_counter.py    # Node counting and validation
  sql_converter.py   # Core conversion entry point
  api_client.py      # Gemini API client
  mapping_sql_generator.py  # SQL generation from column mapping
  local_storage.py   # Local filesystem output storage
  node_cache.py      # Node dict caching
  excel_encrypt.py   # Excel decryption utilities
  test_gemini.py     # Gemini API test script
  requirements.txt    # Python dependencies
  Dockerfile
  .env               # API keys, OUTPUT_DIR (not committed)

visual-testing/      # Playwright E2E tests
```

## Conversion Modes

### Single File Conversion
1. Upload HANA XML file
2. `POST /api/analyze` — count nodes, validate structure
3. `POST /api/start-conversion` — start conversion task
4. Poll `GET /api/conversion-status/<task_id>` every 3s
5. `GET /api/download/<session_id>` — download ZIP (sql or mapping)

### Bulk Conversion
1. Upload ZIP file with multiple XML/TXT files
2. `POST /api/bulk-analyze` — analyze all files, return node counts
3. `POST /api/bulk-conversion` — start bulk task, returns immediately
4. Poll `GET /api/bulk-status/<bulk_task_id>` every 5s
5. `GET /api/bulk-download/<bulk_task_id>` — download all results as ZIP

## Key Differences from SaaS Version

| Feature | SaaS (h2s-may29-v2) | Enterprise (this) |
|---------|---------------------|-------------------|
| Auth | Supabase email/password | None — always authenticated |
| Payments | PhonePe, PayPal, credits | None — unlimited |
| Storage | GCS + Supabase | Local filesystem + memory |
| DB | Supabase PostgreSQL | None |
| AI models | Gemini, DeepSeek | Gemini only |

## Environment Variables

### Backend (`backend/.env`)
```
GEMINI_API_KEY=                    # Required — Google AI Studio key
OUTPUT_DIR=C:\Users\logav\Downloads\Output_h2s_local
```

### Frontend (`frontend/.env.local`)
```
NEXT_PUBLIC_API_BASE_URL=http://localhost:8080
```

## Run Locally

```bash
# Backend
cd backend
pip install -r requirements.txt
python flask_app.py       # Debug mode on :8080

# Frontend
cd frontend
npm run dev              # Next.js on :3000
```

## Deployment

Backend: Docker, Cloud Run. Set `GEMINI_API_KEY` and `OUTPUT_DIR` as environment variables at runtime — not baked into image.

Frontend: Docker, Cloud Run. Set `NEXT_PUBLIC_API_BASE_URL` to the backend URL.

## Debugging

- Backend logs: terminal output (DEBUG level locally)
- Frontend: React DevTools, Network tab
- Gemini API: `python backend/test_gemini.py` — validates key and both model paths
- Conversion output: `OUTPUT_DIR` directory on disk

## Testing
- Gemini test: `backend/test_gemini.py`
- E2E tests: `visual-testing/` (Playwright)
- **Do not** place test files inside `frontend/` or `backend/` — they break Docker builds
