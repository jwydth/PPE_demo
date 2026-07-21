# Smart Factory Safety Monitoring System

FastAPI + SQLModel backend and Next.js frontend for PPE compliance and zone
incursion monitoring.

The current local development stack uses:

- PostgreSQL for structured data
- MinIO for evidence snapshots
- FastAPI for detection, zone configuration, health checks, and incident APIs
- Next.js for image/video upload, zone drawing, tracking overlays, and history

## Local Startup Order

Start the local project in this order:

1. Infrastructure: PostgreSQL and MinIO
2. Streaming: MediaMTX
3. Streaming: FFmpeg RTSP stream
4. Backend: FastAPI
5. Frontend: Next.js

## Infrastructure Setup

Run these commands from PowerShell to start local PostgreSQL and MinIO.

```powershell
cd C:\path\to\PPE_demo
docker compose up -d
docker compose ps
```

Wait until `postgres` shows `healthy`.

## Streaming Setup

Run the streaming setup before starting the backend and frontend.

### Install MediaMTX

MediaMTX is the local RTSP server used by the real-time streaming feature.

1. Open the [MediaMTX releases page](https://github.com/bluenviron/mediamtx/releases).
2. Download the Windows standalone binary `.zip` file from the release assets.
   The file name should look similar to `mediamtx_v1.19.1_windows_amd64.zip`.
3. Extract the `.zip` file.
4. Open the extracted `mediamtx.exe` file.
5. If Windows blocks it, select **More info**, then **Run anyway**.

Keep MediaMTX running while you use the streaming feature.

### Install FFmpeg

Open Windows PowerShell as Administrator, then install FFmpeg with `winget`:

```powershell
winget install -e --id Gyan.FFmpeg
```

Close and reopen PowerShell, then confirm FFmpeg is available:

```powershell
ffmpeg -version
```

### Start the RTSP stream

Open a terminal in the folder that contains your video file, configure and run the FFmpeg command below.
Make sure to configure the command with the attributes below before running:
+ Replace `mp_.mp4` with the actual video filename.
+ The `-r 15` flag sets the output frame rate to 15 FPS. Adjust this value if needed.

```powershell\
ffmpeg -re -stream_loop -1 -i mp_.mp4 -r 15 -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -level 3.0 -g 15 -bf 0 -flags +global_header -f rtsp -rtsp_transport tcp rtsp://localhost:8554/mystream
```

This command keeps running and loops the video into MediaMTX. Leave this
terminal open while using the real-time streaming feature.

After MediaMTX and FFmpeg are running, start the backend and frontend. The
frontend connects to:

```text
rtsp://localhost:8554/mystream
```

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

Then create the backend environment file and initialize tables:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m app.db.init_db
```

If you are using GPU inference, see GPU Runtime Check below and run the
verification script before starting the API.

Start the backend API:

```powershell
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

`--reload-dir app` restricts the auto-reloader to `backend/app` (the source
code) instead of watching the whole `backend/` tree. Without it, every
violation snapshot written to `backend/storage/` during live detection
triggers a full server restart, which drops every open camera WebSocket.

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

Default settings from `backend/.env.example`:

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

`backend/.env` overrides these defaults. If the team is using a different
model file, set `MODEL_PATH` in `backend/.env` and verify that the file exists
under `backend/weights`.

If model weights are missing or unavailable, the detector can run in mock mode
for development.

## GPU Runtime Check

If you are using GPU inference, verify the backend runtime before starting the
API:

```powershell
cd C:\path\to\PPE_demo\backend
.\.venv\Scripts\Activate.ps1
python scripts/check_gpu_runtime.py
```

The script exits successfully even when CUDA is unavailable. In that case it
prints CPU/mock-mode status so the environment problem is visible.

Use the project venv when starting the backend:

```powershell
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

## Useful Backend Commands

Run from `backend` with `.venv` activated:

```powershell
python -m pytest
python -m ruff check app tests
```

Initialize tables only during first setup or after resetting the database:

```powershell
python -m app.db.init_db
```

## Main Backend APIs

- `GET /health`
- `GET /health/db`
- `GET /health/storage`
- `POST /predict`
- `POST /predict-video`
- `POST /zones`
- `GET /zones?video_name=...`
- `PUT /zones/{zone_id}`
- `DELETE /zones/{zone_id}`
- `DELETE /zones/video?video_name=...`
- `GET /violations`
- `GET /zone-violations`

## Documentation

- Backend setup: `backend/docs/backend_setup.md`
- Internal deployment notes: `backend/docs/internal_deployment.md`
- API details: `backend/docs/api_routes.md`
- Storage architecture: `backend/docs/storage_architecture.md`
- Approved database schema: `backend/docs/database_schema.dbml`
