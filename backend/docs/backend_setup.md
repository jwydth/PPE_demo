# Backend Setup

This guide starts the backend from a fresh checkout for the first time.
PostgreSQL stores structured data, and MinIO stores evidence files. Both run
locally through Docker Compose.

## Requirements

Install these before continuing:

- Python 3.11 or another project-compatible Python version
- Docker Desktop or Docker Engine with Docker Compose
- Git
- Optional: NVIDIA CUDA environment for GPU inference

Run the following commands from PowerShell.

## First-Time Setup

### 1. Open the project

```powershell
cd C:\path\to\PPE_demo
```

All paths in the remaining steps are relative to the project root.

### 2. Start PostgreSQL and MinIO

Make sure Docker is running, then execute:

```powershell
docker compose up -d
docker compose ps
```

Wait until the `postgres` service reports that it is healthy before running
database initialization. If it still says `health: starting`, wait a few
seconds and run `docker compose ps` again. The services are
available at:

- PostgreSQL: `localhost:5432`
- MinIO S3 API: `http://localhost:9000`
- MinIO console: `http://localhost:9001`

PostgreSQL and MinIO data remain in the Compose-managed `postgres_data` and
`minio_data` volumes when the containers stop.

### 3. Create and activate the Python environment

```powershell
cd backend
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For CUDA inference, install `requirements-cuda-cu132.txt` before
`requirements.txt`.

### 4. Create the backend environment file

While still in the `backend` directory:

```powershell
Copy-Item .env.example .env
```

The default local connection settings are:

```dotenv
DATABASE_URL=postgresql+psycopg://safety_user:safety_password@localhost:5432/safety_monitoring
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_NAME=safety-monitoring-evidence
MINIO_SECURE=false
```

Do not commit `.env`. Change the development credentials before using this
setup on a shared server.

### 5. Initialize the database

The backend does not create database tables automatically during startup.
Run this once for a new PostgreSQL database:

```powershell
python -m app.db.init_db
```

Expected output:

```text
PostgreSQL tables created successfully.
```

Running this command again is safe when the tables already exist.

### 6. Start the backend

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Keep this terminal open while using the backend. If model weights do not exist
at `MODEL_PATH`, the detector runs in mock mode.

### 7. Verify the installation

Open a second PowerShell terminal and run:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/health/db
Invoke-RestMethod http://localhost:8000/health/storage
```

Expected database response:

```json
{"database":"connected"}
```

Expected storage response:

```json
{"storage":"connected","bucket":"safety-monitoring-evidence"}
```

The API documentation is available at `http://localhost:8000/docs`.

## First Data Flow

No manual seed data is required for normal local development.

When zones are saved from the frontend or through `POST /zones`, the backend
creates the required setup rows automatically:

- A single default factory when one does not exist
- A camera row for the submitted `video_name`
- One `physical_zones` row per drawn real zone
- One `camera_zone_views` row per camera-specific polygon

When video processing finds incidents, the backend creates PPE and zone
violation rows automatically and uploads evidence snapshots to MinIO.

Expected zone behavior:

- Two restricted polygons in the same video are two physical zones.
- `RESTRICTED` is a zone type, not a shared physical-zone identity.
- API `zone_id` is a backward-compatible alias for `camera_zone_view_id`.

## Starting the Project Again

After the first-time setup, start the infrastructure from the project root:

```powershell
docker compose up -d
```

Then start the backend:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --port 8000
```

You do not need to recreate `.venv`, reinstall dependencies, copy `.env`, or
initialize the database each time.

## Stop the Project

Stop PostgreSQL and MinIO while retaining their data:

```powershell
docker compose down
```

The backend process can be stopped with `Ctrl+C`.

The following command also permanently deletes the local PostgreSQL and MinIO
data. Use it only when you intentionally want a completely fresh database and
object store:

```powershell
docker compose down -v
```

After deleting the volumes, repeat the database initialization step.

## Tests and Linting

From the `backend` directory with `.venv` activated:

```powershell
python -m pytest
python -m ruff check app tests
```
