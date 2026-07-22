# HANACV2SQL Enterprise Edition

Standalone local deployment of the HANACV2SQL converter without cloud dependencies (no Supabase, no GCS, unlimited usage).

## Features

- Convert SAP HANA Calculation Views to SQL for BigQuery, Snowflake, Redshift, Databricks, Microsoft Fabric
- Bulk conversion support (ZIP upload)
- SQL Mapping tool for column-level lineage
- AI-enhanced SQL generation with Gemini
- BigQuery SQL validation
- Local filesystem storage (no cloud storage required)

## Prerequisites

- Docker and Docker Compose
- Google AI Studio API key (for Gemini enhancement)
- GCP Service Account with BigQuery access (for SQL validation)

## Quick Start

### 1. Clone and Setup

```bash
# Navigate to project directory
cd h2s-local-deploy

# Copy environment template
cp .env.enterprise.example .env.enterprise
```

### 2. Configure Environment

Edit `.env.enterprise` with your credentials:

```bash
# AI Enhancement (REQUIRED)
GEMINI_API_KEY=your_google_ai_studio_key_here

# BigQuery Validation (REQUIRED)
GOOGLE_APPLICATION_CREDENTIALS=/data/gcp-key.json
BQ_PROJECT_ID=your-gcp-project-id
GCP_REGION=us-central1
```

### 3. GCP Service Account Setup

Create a GCP service account with these roles:
- BigQuery Data Editor
- BigQuery Job User  
- BigQuery Read Session User

Mount the JSON key into the container:

```yaml
# In docker-compose.enterprise.yml
volumes:
  - /path/to/your/service-account-key.json:/data/gcp-key.json:ro
```

### 4. Build and Run

```bash
# Build and start containers
docker-compose -f docker-compose.enterprise.yml up --build

# Run in background
docker-compose -f docker-compose.enterprise.yml up --build -d
```

### 5. Access the Application

- Frontend: http://localhost:3000
- Backend API: http://localhost:8080
- Health Check: http://localhost:8080/health

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│   Frontend      │     │   Backend       │
│   (Next.js)     │────▶│   (Flask)       │
│   Port 3000     │     │   Port 8080     │
└─────────────────┘     └────────┬────────┘
                                 │
                        ┌────────▼────────┐
                        │  Local Storage  │
                        │  /data/outputs  │
                        └─────────────────┘
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| PORT | No | 8080 | Backend port |
| FLASK_ENV | No | production | Flask environment |
| OUTPUT_DIR | No | /data/outputs | Conversion output directory |
| GEMINI_API_KEY | Yes | - | Google AI Studio API key |
| GOOGLE_APPLICATION_CREDENTIALS | Yes | - | Path to GCP service account JSON |
| BQ_PROJECT_ID | Yes | - | GCP project ID for BigQuery |
| GCP_REGION | No | us-central1 | GCP region |

## Usage

### Single File Conversion

1. Open http://localhost:3000
2. Select "HANA Converter"
3. Upload your SAP HANA Calculation View XML file
4. Wait for analysis and conversion
5. Download the converted SQL and mapping sheet

### Bulk Conversion

1. Select "Bulk" mode
2. Upload a ZIP file containing multiple XML/TXT files
3. Review the analysis summary
4. Start bulk conversion
5. Download all converted files as a ZIP

### SQL Mapping

1. Select "SQL Mapping" from the home page
2. Upload your mapping spreadsheet (XLSX)
3. Edit column mappings as needed
4. Generate SQL for your target platform

## Stopping

```bash
# Stop containers (keep volumes)
docker-compose -f docker-compose.enterprise.yml stop

# Stop and remove containers, networks
docker-compose -f docker-compose.enterprise.yml down

# Stop and remove everything including volumes
docker-compose -f docker-compose.enterprise.yml down -v
```

## Troubleshooting

### Backend won't start

Check logs:
```bash
docker-compose -f docker-compose.enterprise.yml logs backend
```

### Frontend build fails

Check Node version - requires Node 20+:
```bash
node --version
```

### BigQuery validation not working

1. Verify GCP credentials are mounted correctly
2. Check service account has required roles
3. Test locally:
```bash
docker-compose -f docker-compose.enterprise.yml exec backend python -c "from google.cloud import bigquery; print('OK')"
```

## Data Persistence

Conversion outputs are stored in `./data/outputs/` on the host machine. This directory is mounted into the backend container.

To backup:
```bash
tar -czf backups/outputs-$(date +%Y%m%d).tar.gz data/outputs/
```

## Security Notes

- The service account key is mounted read-only inside the container
- No data leaves your infrastructure (except Gemini API calls)
- OUTPUT_DIR contains potentially sensitive SQL - protect accordingly

## License

Enterprise license - see your purchase agreement.
