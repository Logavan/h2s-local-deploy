# Backend

This directory contains all backend-related code for the HANACV2SQL application.

## Structure

- `api`: API routes
- `db`: Database models and migrations
- `services`: Business logic services
- `utils`: Backend utility functions
- `workers`: Background workers

## Development

\`\`\`bash
cd backend
pip install -r requirements.txt
python run.py
\`\`\`

## API Documentation

The backend provides the following API endpoints:

- `/api/validate-xml`: Validates XML content
- `/api/convert`: Converts HANA CV to SQL

## Database

The backend uses Supabase for database storage. Database migrations are stored in the `supabase/migrations` directory.
\`\`\`

Let's create a shared README:
