# Smart Factory Safety Monitoring System

FastAPI + SQLModel backend and Next.js frontend for PPE compliance and zone
incursion monitoring.

The current local development stack uses:

- PostgreSQL for structured data
- MinIO for evidence snapshots
- FastAPI for detection, zone configuration, health checks, and incident APIs
- Next.js for image/video upload, zone drawing, tracking overlays, and history

## Infrastructure Setup

Run these commands from PowerShell to start local PostgreSQL and MinIO.

```powershell
cd C:\path\to\PPE_demo
docker compose up -d
docker compose ps
```

Wait until `postgres` shows `healthy`.

## Backend Setup

```powershell
cd backend
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

For GPU inference, install the recommended CUDA 13.2 runtime before the normal
backend requirements:

```powershell
pip install -r requirements-cuda-cu132.txt
pip install -r requirements.txt
```

For CPU-only development, install only the normal backend requirements:

```powershell
pip install -r requirements.txt
```

Then create the backend environment file, initialize tables, and start the API:

```powershell
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

## Frontend Setup

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
MODEL_PATH=weights/ppe_v5.pt
INFERENCE_DEVICE=auto
```

If model weights are missing or unavailable, the detector can run in mock mode
for development.

## Reproducible GPU Runtime Setup

The PPE model was trained in a GPU software stack. Developers do not need the
exact same NVIDIA GPU model, but they should use the same verified Python,
PyTorch CUDA, torchvision, Ultralytics, model weights, and inference device
configuration when running the backend.

Official recommended runtime:

```text
Python 3.11.x (verified with Python 3.11.9)
torch==2.12.0+cu132
torchvision==0.27.0+cu132
ultralytics==8.4.58
torch CUDA build == 13.2
MODEL_PATH=weights/ppe_v5.pt
INFERENCE_DEVICE=auto
```

Use this setup for the standard reproducible PPE model runtime:

```powershell
cd C:\path\to\PPE_demo\backend
.\.venv\Scripts\Activate.ps1
pip install -r requirements-cuda-cu132.txt
pip install -r requirements.txt
```

If cu132 fails on a developer machine, do not silently install another CUDA
stack. Ask the team first, verify the new runtime, and document the new
approved setup before changing dependency files.

All developers should run the verification script before starting the backend:

```powershell
cd C:\path\to\PPE_demo\backend
.\.venv\Scripts\Activate.ps1
python scripts/check_gpu_runtime.py
```

Expected verified runtime values:

```text
torch version: 2.12.0+cu132
torch CUDA build: 13.2
CUDA available: True
Selected detector device: cuda:0
YOLO model loaded: True
Backend using mock mode: False
```

## GPU Runtime Verification

Run this from the backend directory with the project venv activated:

```powershell
python scripts/check_gpu_runtime.py
```

Example successful GPU output:

```text
CUDA available: True
CUDA device count: 1
GPU: NVIDIA RTX ...
Selected detector device: cuda:0
YOLO model loaded: True
Backend using mock mode: False
```

The script exits successfully even when CUDA is unavailable. In that case it
prints CPU/mock-mode status so the environment problem is visible.

IMPORTANT: running plain `python` may use a different Python installation and
a CPU-only PyTorch build. Always activate the project venv or call the venv
Python directly before starting the backend:

```powershell
.\.venv\Scripts\Activate.ps1
```

or:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

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
