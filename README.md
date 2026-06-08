# Smart Factory Safety Monitoring System

FastAPI + SQLModel backend and Next.js frontend for PPE compliance and zone
incursion monitoring.

The current local development stack uses:

- PostgreSQL for structured data
- MinIO for evidence snapshots
- FastAPI for detection, zone configuration, health checks, and incident APIs
- Next.js for image/video upload, zone drawing, tracking overlays, and history

## Quick Start

Run these commands from PowerShell.

### 1. Start PostgreSQL and MinIO

```powershell
cd C:\path\to\PPE_demo
docker compose up -d
docker compose ps
```

Wait until `postgres` shows `healthy`.

### 2. Start the backend

```powershell
cd backend
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.db.init_db
python -m uvicorn app.main:app --reload --port 8000
```

Backend URLs:

- API: `http://localhost:8000`
- Swagger docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`
- PostgreSQL health: `http://localhost:8000/health/db`
- MinIO health: `http://localhost:8000/health/storage`

### 3. Start the frontend

Open another PowerShell terminal:

```powershell
cd C:\path\to\PPE_demo\frontend
npm install
Copy-Item .env.local.example .env.local
npm run dev
```

Frontend URL:

- `http://localhost:3000`

## First Data Flow

No manual database seed is required for normal local use.

The first time you save zones from the frontend, the backend automatically
creates:

- default factory
- camera row for the uploaded video/source name
- physical zone rows
- camera-zone-view rows

The first time video detection finds incidents, the backend automatically
creates:

- PPE violation rows
- PPE violation subject rows
- zone violation rows
- MinIO evidence objects

Expected zone behavior:

- One drawn polygon represents one physical zone.
- Two restricted polygons in the same video create two `physical_zones`.
- `zone_type = RESTRICTED` is a category, not a shared zone identity.
- API `zone_id` is kept for frontend compatibility and maps to
  `camera_zone_view_id`.

## Local Reset

For local development only, this deletes local PostgreSQL and MinIO data but
does not delete source code:

```powershell
cd C:\path\to\PPE_demo
docker compose down -v
docker compose up -d
docker compose ps

cd backend
python -m app.db.init_db
```

Wait until PostgreSQL is healthy before running `python -m app.db.init_db`.

## Backend Configuration

Backend config is loaded from `backend/.env`.

Default local settings:

```dotenv
DATABASE_URL=postgresql+psycopg://safety_user:safety_password@localhost:5432/safety_monitoring
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_NAME=safety-monitoring-evidence
MINIO_SECURE=false
MODEL_PATH=weights/ppe_v4.pt
INFERENCE_DEVICE=auto
```

If model weights are missing or unavailable, the detector can run in mock mode
for development.

## Useful Backend Commands

Run from `backend` with `.venv` activated:

```powershell
python -m app.db.init_db
python -m pytest
python -m ruff check app tests
```

## Main Backend APIs

- `GET /health`
- `GET /health/db`
- `GET /health/storage`
- `POST /predict`
- `POST /predict-video`
- `POST /zones`
- `GET /zones/{video_name}`
- `PUT /zones/{zone_id}`
- `DELETE /zones/{zone_id}`
- `DELETE /zones/video/{video_name}`
- `GET /violations`
- `GET /zone-violations`

## Documentation

- Backend setup: `backend/docs/backend_setup.md`
- Internal deployment notes: `backend/docs/internal_deployment.md`
- API details: `backend/docs/api_routes.md`
- Storage architecture: `backend/docs/storage_architecture.md`
- Approved database schema: `backend/docs/database_schema.dbml`
