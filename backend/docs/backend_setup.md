# Backend Setup

## Requirements

- Python 3.11 or another project-compatible Python version
- A Neon PostgreSQL database
- MinIO, either local or remotely hosted
- Optional NVIDIA CUDA environment for GPU inference

Run all commands from the `backend` directory unless stated otherwise.

## Create the Python Environment

PowerShell:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For the repository's documented CUDA environment, install
`requirements-cuda-cu128.txt` before `requirements.txt`.

## Environment Variables

Create `backend/.env` from `.env.example`.

Required database and storage settings:

```dotenv
DATABASE_URL=postgresql+psycopg://username:password@host/database?sslmode=require

MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET_NAME=safety-monitoring-evidence
MINIO_SECURE=false
```

Important application settings:

```dotenv
MODEL_PATH=weights/ppe_v1.pt
INFERENCE_DEVICE=auto
CONFIDENCE_THRESHOLD=0.5
VIDEO_FRAME_STRIDE=1
SNAPSHOT_DIR=storage/snapshots
ALLOWED_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
```

`SNAPSHOT_DIR` is optional because the application defaults to
`storage/snapshots`. It can still be set in `.env`.

Do not commit `.env`. It contains database and MinIO credentials.

## Neon PostgreSQL

1. Create a Neon project and database.
2. Copy the connection string from the Neon dashboard.
3. Use the Psycopg SQLAlchemy driver form:

```text
postgresql+psycopg://username:password@host/database?sslmode=require
```

4. Set that value as `DATABASE_URL` in `backend/.env`.
5. Create the SQLModel tables manually:

```powershell
python -m app.db.init_db
```

Expected output:

```text
PostgreSQL tables created successfully.
```

The application does not automatically call `create_all()` during startup.

## Start MinIO Locally

### Docker

Create a persistent data directory and start MinIO:

```powershell
docker run --name safety-minio `
  -p 9000:9000 `
  -p 9001:9001 `
  -e MINIO_ROOT_USER=minioadmin `
  -e MINIO_ROOT_PASSWORD=minioadmin `
  -v minio-data:/data `
  minio/minio server /data --console-address ":9001"
```

Services:

- S3-compatible API: `http://localhost:9000`
- MinIO console: `http://localhost:9001`

The backend automatically creates `MINIO_BUCKET_NAME` if it does not exist.

For production, use strong credentials, TLS, and restricted network access.
Set `MINIO_SECURE=true` when the endpoint uses HTTPS.

## Run the Backend

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Local URLs:

- API: `http://127.0.0.1:8000`
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

If model weights do not exist at `MODEL_PATH`, the detector runs in mock mode.

## Check Health

Application:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

PostgreSQL:

```powershell
Invoke-RestMethod http://localhost:8000/health/db
```

Expected:

```text
database
--------
connected
```

MinIO:

```powershell
Invoke-RestMethod http://localhost:8000/health/storage
```

Expected:

```text
storage   bucket
-------   ------
connected safety-monitoring-evidence
```

## Basic API Checks

List PPE violations:

```powershell
Invoke-RestMethod http://localhost:8000/violations
```

List zone violations:

```powershell
Invoke-RestMethod http://localhost:8000/zone-violations
```

List zones for a source:

```powershell
Invoke-RestMethod "http://localhost:8000/zones/factory.mp4"
```

Upload an image:

```powershell
curl.exe -X POST "http://localhost:8000/predict" `
  -F "file=@C:\path\to\image.jpg;type=image/jpeg"
```

Upload a video:

```powershell
curl.exe -X POST "http://localhost:8000/predict-video" `
  -F "file=@C:\path\to\video.mp4;type=video/mp4"
```

## Run Tests and Linting

Run all tests:

```powershell
python -m pytest
```

Run Ruff:

```powershell
python -m ruff check app tests
```

## Storage Notes

- PostgreSQL contains structured application records.
- MinIO contains persisted evidence snapshots.
- `storage/snapshots` is a local workspace and compatibility directory.
- `/snapshots` remains available for older local snapshot URLs.
- SQLite is not part of the current runtime architecture.
