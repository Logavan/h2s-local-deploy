# HANACV2SQL Project

## Overview
SaaS tool that converts SAP HANA Calculation Views to SQL queries. Migrates HANA → BigQuery, Snowflake, Redshift, Databricks, Microsoft Fabric.

## Tech Stack

### Frontend (Next.js 16 App Router)
- **Framework**: Next.js 16 with TypeScript
- **Styling**: Tailwind CSS
- **Auth**: Supabase Auth
- **Database**: Supabase (PostgreSQL)
- **State**: React Context (AuthContext)
- **Deployment**: Cloud Run (standalone Docker)

### Backend (Flask/Python)
- **Framework**: Flask
- **API Client**: Python with BigQuery, GCS integrations
- **DB**: Google BigQuery, Supabase

## Project Structure
```
frontend/           # Next.js app (app router)
  app/             # Pages, API routes, layouts
  components/      # React components
    ui/            # shadcn/ui components
    conversion/    # File upload, conversion status
    account/       # Conversion history, purchase history
  lib/             # Utilities, API client, Supabase helpers
  hooks/           # Custom React hooks
  contexts/        # React contexts (Auth)
  styles/          # Tailwind globals
  public/          # Static assets

backend/           # Flask API
  flask_app.py     # Main Flask app (PRD)
  bulk_processor.py # Bulk ZIP file processing
  api_client.py    # External API calls (BigQuery, Gemini, DeepSeek)
  file_processor.py # XML to SQL conversion logic
  node_counter.py  # Node counting and validation
  requirements.txt  # Python dependencies
  Dockerfile

scripts/           # Deployment scripts
supabase/          # DB migrations & schema
visual-testing/    # Frontend/backend testing (Playwright, manual QA)
```

## Conversion Modes

### Single File Conversion
- Upload one XML file at a time
- Immediate analysis and conversion
- Progress shown via polling

### Bulk Conversion
- Upload ZIP file containing multiple XML/TXT files
- Automatic analysis of all files
- Real-time progress dashboard
- Download all results as single ZIP

## Key Patterns

### Frontend → Backend API Calls
- Frontend calls backend API at `NEXT_PUBLIC_API_BASE_URL`
- Backend at `https://backend-prd-*.run.app`
- Local dev: `http://localhost:8080`

### Supabase Usage
- Frontend: `@supabase/supabase-js` via `frontend/lib/supabase.ts`
- Server: Admin access via `SUPABASE_SERVICE_ROLE_KEY`
- Auth: Email/password via Supabase Auth

## Conversion Flow

### Single File Flow
1. User uploads HANA XML file
2. Frontend validates file type and size
3. `analyzeXmlFile()` - Backend analyzes XML structure
4. User confirms conversion type (Free/Paid)
5. `startConversion()` - Backend starts conversion task
6. Frontend polls `getConversionStatus()` every 3 seconds
7. On completion, `downloadConvertedFile()` fetches ZIP
8. Credits deducted via Supabase

### Bulk Conversion Flow
1. User uploads ZIP file
2. Frontend extracts and validates files
3. `analyzeBulkZip()` - Backend analyzes all files in ZIP
4. Summary shows: total files, free/paid breakdown, credits needed
5. User clicks "Start Bulk Conversion"
6. `startBulkConversion()` - Backend creates bulk task, returns immediately
7. Frontend polls `getBulkConversionStatus()` every 5 seconds
8. Real-time dashboard shows progress per file
9. On completion, `downloadBulkResult()` fetches ZIP
10. Credits deducted per completed file

## Credit Tiers
| Nodes | Free Conversions | Paid Credits |
|-------|-----------------|--------------|
| 1-10 | 5 per day | 10 credits |
| 11-20 | 0 | 10 credits |
| 21-40 | 0 | 20 credits |
| 41+ | 0 | 30 credits |

## Important Tables (Supabase)
- `users` - User accounts and credit balance
- `conversions` - Conversion history & metadata
- `purchases` - Credit purchases
- `bulk_tasks` - Bulk conversion task status
- `bulk_results` - Per-file bulk conversion results
- `displayed_reviews` - Static testimonial content

## Database Flow

### During Bulk Conversion
1. **Task Created** - `bulk_tasks` table entry with status "PROCESSING"
2. **Per-File Records** - Each file creates `conversions` record
3. **Immediate Updates** - Completed files update immediately
4. **Polling Returns Fresh Data** - Frontend always sees latest state
5. **Credits Deducted** - Only for successfully completed files

## Environment Variables
- `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` - Frontend Supabase
- `SUPABASE_SERVICE_ROLE_KEY` - Backend admin access
- `NEXT_PUBLIC_API_BASE_URL` - Backend API endpoint
- `GEMINI_API_KEY` / `DEEPSEEK_API_KEY` - AI for enhanced conversion

## Conventions
- Use TypeScript strict mode
- All API routes return JSON
- Credit deduction is server-side only
- Conversion history stored in Supabase
- Server actions in `frontend/app/actions/`
- Component imports: use path aliases (`@/components/...`)

## Debugging
- Backend logs: Cloud Run → Log Explorer
- Frontend: React DevTools, Network tab
- Supabase: Dashboard → Table Editor
- Credits: `frontend/lib/credit-audit.ts` for debugging
