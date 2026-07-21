# HANACV2SQL

A tool to convert HANA Calculation Views to SQL.

## Project Structure

This project is organized into three main parts:

### Frontend
- `frontend/app`: Next.js app directory
- `frontend/components`: React components
- `frontend/contexts`: React contexts
- `frontend/hooks`: React hooks
- `frontend/lib`: Frontend utilities
- `frontend/public`: Static assets
- `frontend/styles`: CSS and styling
- `frontend/types`: TypeScript types
- `frontend/utils`: Frontend utility functions

### Backend
- `backend/api`: API routes
- `backend/db`: Database models and migrations
- `backend/services`: Business logic services
- `backend/utils`: Backend utility functions
- `backend/workers`: Background workers

### Shared
- `shared/constants`: Shared constants
- `shared/types`: Shared TypeScript types
- `shared/utils`: Shared utility functions

### Other
- `supabase`: Supabase configuration and migrations
- `scripts`: Build and utility scripts

## Development

### Prerequisites

- Node.js 18+
- Python 3.9+
- Docker and Docker Compose (optional)

### Running with Docker

\`\`\`bash
# Start both frontend and backend
docker-compose up

# Start only the backend
docker-compose up backend

# Start only the frontend
docker-compose up frontend
\`\`\`

### Running without Docker

#### Backend

\`\`\`bash
cd backend
pip install -r requirements.txt
python run.py
\`\`\`

#### Frontend

\`\`\`bash
cd frontend
npm install
npm run dev
\`\`\`

## Environment Variables

Create a `.env.local` file in the root directory:

\`\`\`
NEXT_PUBLIC_API_BASE_URL=http://localhost:5000
\`\`\`

## Deployment

\`\`\`bash
npm run build
