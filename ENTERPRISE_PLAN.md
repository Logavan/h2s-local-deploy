# HANACV2SQL Enterprise — Local Deployment Plan

## Current State

All clutter deletion is **COMPLETE**.

### What Was Deleted (Done)
- backend-testing/, git-commit-last/, visual-testing/, temp/, mobile-screenshots/, scripts/, session_cache/
- frontend/.next/, frontend/session_cache/, backend/__pycache__/, backend/session_cache/
- All frontend pages: login, signup, account, blog, documentation, pricing, help-center, etc.
- All frontend marketing components: testimonials, reviews, features, FAQ, integrations, etc.
- All frontend Supabase libs: supabase.ts, auth-service.ts, conversions.ts, credit-*.ts, etc.
- All backend Supabase services: zepto_mail.py, notification_handler.py
- All GCP SA keys: dev-hanacv2sql-*.json
- All env files: .env, .env.dev, .env.prd, .env.local, .flaskenv
- All error logs, debug logs, raw output files
- Docker files: Dockerfile, .dockerignore (frontend and backend)
- Git history: .git/

---

## TASK BREAKDOWN (21 Tasks)

---

## PHASE 1: BACKEND - New Files to Create

### Task 1.1: Create `backend/local_storage.py`
**Purpose:** Replace GCS and Supabase with local filesystem storage

**Specific functions to implement:**
- `save_result(task_id, zip_content, mapping_content, metadata)` → saves to OUTPUT_DIR
- `get_result_url(task_id, file_type)` → returns local file path
- `cleanup_old_results(max_age_hours=24)` → cleanup job

---

### Task 1.2: Create `backend/enterprise_flask_app.py`
**Purpose:** Clean fork without Supabase/GCS/credits

**REMOVE these imports:**
- `from supabase import create_client, Client`
- `from gcs_upload import upload_to_gcs`
- `from supabase_services.zepto_mail import send_email`

**REMOVE:**
- Lines 60-61: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
- Lines 92-96: supabase client initialization
- Lines 101-146: lazy_cleanup_check function
- Lines 349-365: daily_free_conversions_used query
- Lines 528-578: concurrency control with supabase
- Lines 641-658: Supabase insert for conversions
- Lines 706-714: reset is_conversion_running
- Lines 881-902: Supabase fallback in conversion-status
- Lines 919-921: supabase check in download endpoint
- Lines 970-1000: Supabase fallback in download

**ADD:**
- Import local_storage module
- Replace GCS uploads with local_storage.save_result()
- Replace download URLs with local file serving

---

### Task 1.3: Create `backend/Dockerfile.enterprise`
**Multi-stage build:**
- Stage 1: `python:3.11-slim` for builder
- Stage 2: `python:3.11-alpine` for runtime
- Copy only necessary files
- Set WORKDIR /app
- Expose PORT 8080
- CMD: `["python", "enterprise_flask_app.py"]`

---

### Task 1.4: Create `.env.enterprise.example`

```
PORT=8080
FLASK_ENV=production
OUTPUT_DIR=/data/outputs

# === AI Enhancement (REQUIRED) ===
GEMINI_API_KEY=your_google_ai_studio_key_here

# === BigQuery Validation (REQUIRED) ===
GOOGLE_APPLICATION_CREDENTIALS=/data/gcp-key.json   # mount your SA key here
BQ_PROJECT_ID=your-gcp-project-id
GCP_REGION=us-central1
```

---

## PHASE 2: BACKEND - Modifications to Existing Files

### Task 2.1: Modify `backend/flask_app.py`

**Remove imports:**
1. Line 16: `from supabase import create_client, Client`
2. Line 32: `from gcs_upload import upload_to_gcs`
3. Line 40: `from supabase_services.zepto_mail import send_email`

**Remove env var loading:**
4. Lines 60-61: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

**Remove supabase client init:**
5. Lines 92-96: `supabase: Client = create_client(...)`

**Remove lazy_cleanup_check:**
6. Lines 101-146: Delete entire function
7. Line 344: Remove threading call

**Remove daily_free_conversions_used query:**
8. Lines 349-365: In analyze_xml() - simplify without DB query

**Remove concurrency control:**
9. Lines 528-578: In _perform_conversion_task - remove supabase.table("users") calls

**Replace GCS upload:**
10. Lines 635-636: Change `upload_to_gcs()` to `local_storage.save_result()`

**Remove Supabase insert:**
11. Lines 641-658: Remove conversion record insert

**Remove admin notification:**
12. Lines 794-827: Remove send_admin_notification function and threading call

**Remove Supabase fallback:**
13. Lines 881-902: In conversion-status endpoint

**Remove Supabase check in download:**
14. Lines 919-921: Remove supabase availability check

**Replace download logic:**
15. Lines 970-1000: Change from GCS URLs to local file serving

---

### Task 2.2: Modify `backend/bulk_processor.py`

**Remove import:**
1. Line 21: `from supabase import create_client`

**Remove supabase init:**
2. Lines 24-27: Remove SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, supabase client

**Remove daily_free_count query:**
3. Lines 344-354: In analyze_zip() method

**Replace GCS uploads:**
4. Lines 95-96: Change `upload_to_gcs()` to `local_storage.save_result()`

**Remove Supabase insert:**
5. Lines 100-116: Remove `supabase.table("conversions").insert()`

**Remove concurrency control:**
6. Lines 419-456: Remove is_conversion_running set/get logic

**Remove concurrency reset:**
7. Lines 510-520: Remove is_conversion_running reset in finally block

---

### Task 2.3: Modify `backend/requirements.txt`

**REMOVE:**
- Line 17: `supabase>=2.10.0`
- Line 35: `google-cloud-storage>=2.18.0`

**KEEP:**
- `google-cloud-bigquery>=3.20.0` (for SQL validation)

---

### Task 2.4: Modify `backend/config/settings.py`

- Remove Supabase config section
- Remove GCS config section
- Add OUTPUT_DIR config

---

## PHASE 3: FRONTEND - New Files to Create

### Task 3.1: Create `frontend/contexts/EnterpriseContext.tsx`

**Implement:**
```typescript
interface EnterpriseContextType {
  isEnterprise: true;
  isAuthenticated: true; // always true (anonymous unlimited)
  user: null; // no user
  credits: Infinity; // unlimited
  // ... helper methods that do nothing or return defaults
}
```

---

### Task 3.2: Create `frontend/Dockerfile.enterprise`

- Node 20 Alpine multi-stage build
- Install pnpm
- Build Next.js app
- Production runtime

---

## PHASE 4: FRONTEND - Modifications

### Task 4.1: Modify `frontend/app/layout.tsx`

**Changes:**
1. Replace `import { AuthProvider } from "@/contexts/AuthContext"` → `import { EnterpriseProvider } from "@/contexts/EnterpriseContext"`
2. Replace `<AuthProvider>` → `<EnterpriseProvider>`
3. Remove `ConnectionManager` import and component
4. Remove `ClientFloatingSupportButton` import and component

---

### Task 4.2: Modify `frontend/app/page.tsx`

**Replace entire content with:**
```typescript
// Simple page with two cards:
// 1. HANA Converter Card → links to /converter
// 2. SQL Mapping Card → links to /mapper
```

---

### Task 4.3: Modify `frontend/components/Header.tsx`

**Simplify to:**
1. Remove all menuItems (How It Works, Features, Pricing, Help Center)
2. Remove login/signup buttons
3. Remove account dropdown
4. Remove user info, credits display
5. Keep: Logo + "Enterprise Edition" badge only

---

### Task 4.4: Modify `frontend/components/HomeClient.tsx`

**Simplify:**
1. Remove all marketing sections (Slider, Features, Benefits, etc.)
2. Keep only the tools section
3. Remove TabSwitcher
4. Two large cards: "HANA Converter" and "SQL Mapping"
5. Each card links to its respective tool

---

### Task 4.5: Modify `frontend/components/ConversionTool.tsx`

**Remove:**
1. Line 9: `import { useAuth } from "@/contexts/AuthContext"`
2. Line 188: `const { session, credits, refreshCredits } = useAuth()`
3. Lines 211-215: refreshCredits useEffect
4. Lines 311-334: validatePrerequisites() - remove isLoggedIn and user?.email checks
5. Lines 312-315: Remove router.push("/login")
6. Remove all credit checks (credits < creditCost)
7. Remove all credit display props in ConversionHeader
8. Remove showNeedCreditsPopup logic
9. Remove showFreeLimitExceededPopup logic
10. Remove ConversionLimitsPopup

---

### Task 4.6: Modify `frontend/components/MappingTool.tsx`

**Remove:**
1. Line 29: `import { useAuth } from "@/contexts/AuthContext"`
2. Line 68: `const { user } = useAuth()`
3. Lines 691-695: router.push('/login') in history button
4. Remove ConversionHistoryModal (or make it work without auth)

---

### Task 4.7: Modify `frontend/lib/api.ts`

**Remove userEmail from these functions:**
- analyzeXmlFile (line 127): Remove userEmail param
- startConversion (line 182): Remove userEmail param
- startBulkConversion (line 609): Remove userEmail param
- checkConversionRunningStatus (line 731): Remove userEmail param
- analyzeBulkZip (line 517): Remove userEmail param

**Update backend calls to not send email in body.**

---

### Task 4.8: Delete `frontend/app/tools/` directory

---

## PHASE 5: Docker & Documentation

### Task 5.1: Create `docker-compose.enterprise.yml`

- Service: frontend (port 3000)
- Service: backend (port 8080)
- Volume: ./data/outputs
- Volume: ./gcp-key.json (optional, for BigQuery)

---

### Task 5.2: Create `data/outputs/.gitkeep`

---

### Task 5.3: Create `README.ENTERPRISE.md`

- Quick start guide
- Environment setup
- GCP BigQuery setup
- Docker commands

---

## Summary Checklist

### PHASE 1: Backend - New Files
- [ ] Task 1.1: Create `backend/local_storage.py`
- [ ] Task 1.2: Create `backend/enterprise_flask_app.py`
- [ ] Task 1.3: Create `backend/Dockerfile.enterprise`
- [ ] Task 1.4: Create `.env.enterprise.example`

### PHASE 2: Backend - Modifications
- [ ] Task 2.1: Modify `backend/flask_app.py` (15 removal points)
- [ ] Task 2.2: Modify `backend/bulk_processor.py` (7 removal points)
- [ ] Task 2.3: Modify `backend/requirements.txt` (2 removals)
- [ ] Task 2.4: Modify `backend/config/settings.py`

### PHASE 3: Frontend - New Files
- [ ] Task 3.1: Create `frontend/contexts/EnterpriseContext.tsx`
- [ ] Task 3.2: Create `frontend/Dockerfile.enterprise`

### PHASE 4: Frontend - Modifications
- [ ] Task 4.1: Modify `frontend/app/layout.tsx` (4 changes)
- [ ] Task 4.2: Modify `frontend/app/page.tsx` (REPLACE)
- [ ] Task 4.3: Modify `frontend/components/Header.tsx` (heavy simplification)
- [ ] Task 4.4: Modify `frontend/components/HomeClient.tsx` (heavy simplification)
- [ ] Task 4.5: Modify `frontend/components/ConversionTool.tsx` (10 removal points)
- [ ] Task 4.6: Modify `frontend/components/MappingTool.tsx` (4 removal points)
- [ ] Task 4.7: Modify `frontend/lib/api.ts` (5 function param removals)
- [ ] Task 4.8: Delete `frontend/app/tools/` directory

### PHASE 5: Docker & Documentation
- [ ] Task 5.1: Create `docker-compose.enterprise.yml`
- [ ] Task 5.2: Create `data/outputs/.gitkeep`
- [ ] Task 5.3: Create `README.ENTERPRISE.md`

---

## External Service Dependencies

| Service       | Env Var                           | Decision                         |
|---------------|-----------------------------------|----------------------------------|
| Gemini        | GEMINI_API_KEY                    | USER PROVIDES — required         |
| BigQuery      | GOOGLE_APPLICATION_CREDENTIALS     | USER PROVIDES — required         |
|               | BQ_PROJECT_ID                     | USER PROVIDES — required         |
| GCS           | —                                 | REMOVED — outputs go to OUTPUT_DIR |
| Supabase      | —                                 | REMOVED — no database           |
| PhonePe       | —                                 | REMOVED — no payments           |
| PayPal        | —                                 | REMOVED — no payments           |

---

## Deployment

### Quick Start

1. cp .env.enterprise.example .env.enterprise
2. Edit .env.enterprise with your keys
3. docker-compose -f docker-compose.enterprise.yml up --build
4. Open http://localhost:3000

### GCP Service Account for BigQuery

Create GCP service account with roles:
- BigQuery Data Editor
- BigQuery Job User
- BigQuery Read Session User

Mount JSON key into container:
```yaml
volumes:
  - ./gcp-key.json:/data/gcp-key.json:ro
environment:
  - GOOGLE_APPLICATION_CREDENTIALS=/data/gcp-key.json
```

---

## Possible Future Enhancements

1. Local SQLite DB for conversion history
2. Admin dashboard at /admin
3. Custom branding / white-labeling
4. Prometheus metrics endpoint
5. Output file organization by date
