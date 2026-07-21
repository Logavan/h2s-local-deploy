# Backend

## Tech Stack
- Flask (Python 3)
- BigQuery, GCS for data processing
- Supabase for metadata storage

## Key Files
```
flask_app.py          # Main Flask app (PRD) - USE THIS
bulk_processor.py     # Bulk ZIP file analysis and processing
file_processor.py     # XML to SQL conversion logic
node_counter.py       # Node counting and validation
api_client.py         # BigQuery, Gemini, DeepSeek API clients
requirements.txt      # Python dependencies
Dockerfile
```

## API Routes

### Main Endpoints

#### Single File Conversion
```
POST /api/analyze         # Analyze XML, count nodes, validate structure
POST /api/start-conversion # Start conversion task, returns task_id
GET  /api/conversion-status/{task_id}  # Poll conversion status
GET  /api/download/{session_id}        # Download converted file
```

#### Bulk Conversion
```
POST /api/bulk-analyze           # Analyze ZIP file, count all nodes
POST /api/bulk-conversion        # Start bulk conversion, returns bulk_task_id
GET  /api/bulk-status/{task_id} # Poll bulk conversion status
GET  /api/bulk-download/{task_id} # Download all converted files
```

#### Utility
```
GET  /api/health                # Health check
GET  /api/debug-env             # Print environment variables (dev only)
GET  /api/test-supabase-admin   # Test Supabase connection (dev only)
```

## Bulk Processing Flow

### 1. Bulk Analyze (`POST /api/bulk-analyze`)
```
Input: multipart/form-data with ZIP file and email
Process:
  1. Extract all XML/TXT files from ZIP
  2. For each file:
     - Count nodes
     - Determine complexity
     - Calculate credit cost
     - Check free conversion eligibility
  3. Check user's daily free conversions used
  4. Apply free limit (5 per day max)
  5. Return summary

Response:
{
  success: true,
  files: [{ file_name, node_count, credit_cost, conversion_type, id }],
  total_files: 50,
  total_nodes: 234,
  total_credits: 450,
  free_count: 5,
  paid_count: 45
}
```

### 2. Start Bulk Conversion (`POST /api/bulk-conversion`)
```
Input: JSON with files array and email
Process:
  1. Validate user credits
  2. Create bulk_task record in database
  3. Queue each file for conversion
  4. Return immediately with task_id

Response:
{
  success: true,
  bulk_task_id: "uuid",
  message: "Bulk conversion started for 50 files"
}
```

### 3. Poll Bulk Status (`GET /api/bulk-status/{task_id}`)
```
Response:
{
  status: "PROCESSING" | "COMPLETED" | "PARTIAL" | "FAILED",
  progress: 75,
  total_files: 50,
  completed_files: 37,
  failed_files: 2,
  results: [
    { file_name, status: "completed" | "failed", sql_url?, error? },
    ...
  ],
  message: "37 completed, 2 failed, 11 processing"
}
```

### 4. Download Bulk Results (`GET /api/bulk-download/{task_id}`)
```
Response: ZIP file download containing all converted SQL files
Filename: "Bulk conversion_YYYY-MM-DDTHH-MM-SS.zip"
```

## Credit Calculation

### Free Conversions
- 5 free conversions per user per day
- Only files with ≤10 nodes are eligible
- Checked against `conversions` table for current date

### Credit Deduction
- Deducted per completed file (not per attempted file)
- Failed files do NOT charge credits
- Partial success: only charged for completed files

### Credit Tiers
| Nodes | Credit Cost |
|-------|-------------|
| 1-10 | 0 (if free available) or 10 |
| 11-20 | 10 |
| 21-40 | 20 |
| 41+ | 30 |

## Database Updates During Bulk Conversion

### Per-File Updates
When each file completes:
1. Update `conversions` table with status
2. Store SQL file URL/path
3. Deduct credits if Paid conversion

### Task Status
- `PENDING` - Task created, not started
- `PROCESSING` - Actively converting files
- `COMPLETED` - All files done (no failures)
- `PARTIAL` - Done with some failures
- `FAILED` - Task failed completely

## Error Handling

### Validation Errors
Returned to frontend with structured error:
```json
{ "error": "Root View:ColumnView element not found", "success": false }
```

### Internal Errors
Logged server-side, generic message to frontend:
```json
{ "error": "Something went wrong. Please try again.", "success": false }
```

### File-Level Errors
Stored in results array:
```json
{ "file_name": "bad.xml", "status": "failed", "error": "Invalid XML structure" }
```

## AI Enhancement
Uses Gemini/DeepSeek APIs for improved SQL conversion quality when `GEMINI_API_KEY` or `DEEPSEEK_API_KEY` is set.

## Environment Variables
```
FLASK_APP=flask_app.py
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
GEMINI_API_KEY
DEEPSEEK_API_KEY
GOOGLE_APPLICATION_CREDENTIALS   # For BigQuery
```

## Run Locally
```bash
cd backend
pip install -r requirements.txt
python run.py  # Uses waitress WSGI server
```

## Deployment
Built as Docker container and deployed to Cloud Run. See `Dockerfile` and `scripts/deploy_prd.sh`.

## Logging
Uses Python `logging` module. Check Cloud Run logs for production debugging.

## Testing
- **DO NOT** place test files inside `backend/` folder
- All frontend/backend testing goes in `visual-testing/` at project root
- This avoids Docker build issues (test files shouldn't be in Docker context)
- Use `visual-testing/test_bulk_conversion.py` for bulk endpoint testing
