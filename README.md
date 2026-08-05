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

1. Infrastructure: PostgreSQL, MinIO, and MediaMTX
2. Streaming: LL-HLS readiness
3. Streaming: FFmpeg RTSP stream
4. Backend: FastAPI
5. Frontend: Next.js

## Infrastructure Setup

Run these commands from PowerShell to start local PostgreSQL, MinIO, and the
configured MediaMTX LL-HLS server.

```powershell
cd C:\path\to\PPE_demo
docker compose up -d
docker compose ps
```

Wait until `postgres` shows `healthy`.

## Streaming Setup

Run the streaming setup before starting the backend and frontend.

### MediaMTX LL-HLS

Docker Compose starts MediaMTX with [`mediamtx.yml`](mediamtx.yml). It accepts
RTSP publishers on port `8554` and serves LL-HLS on port `8888`.

Use either Docker Compose or the standalone `mediamtx.exe`, never both. The
normal project command is:

```powershell
docker compose up -d mediamtx
```

If port `8888` or `8554` is already in use, stop the old standalone MediaMTX
process before starting the container. The standalone executable is only an
alternative for machines that do not use Docker.

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

Use the checked-in publisher script. It normalizes the source to CFR 24 FPS,
H.264, GOP 24, and no B-frames so LL-HLS emits one-second independently
decodable segments and the behavior timeline remains at its trained cadence.

```powershell
.\scripts\publish-rtsp.ps1 -InputPath C:\videos\camera1.mp4 -StreamName stream1
```

The publisher defaults to 24 FPS, a maximum width of 1920 pixels, CRF 23,
an 8 Mbps peak rate, and a 16 Mbit rate-control buffer. These limits prevent
high-resolution IDR bursts from overflowing MediaMTX reader queues. Override
them only when the source or network requires it, for example:

```powershell
.\scripts\publish-rtsp.ps1 -InputPath C:\videos\camera1.mp4 -StreamName stream1 -MaxWidth 1280 -MaxRate 6M -RateControlBuffer 12M
```

This command keeps running and loops the video into MediaMTX. Leave this
terminal open while using the real-time streaming feature.

After MediaMTX and FFmpeg are running, start the backend and frontend. The
frontend connects to:

```text
rtsp://localhost:8554/mystream
```

The original, unannotated LL-HLS playlist is:

```text
http://localhost:8888/mystream/index.m3u8
```

When the dashboard opens the camera, the backend runs the asynchronous AI
workers, delays source frames by three seconds, burns the available Pose,
Behavior, PPE, and Sign results into each frame, and publishes:

```text
rtsp://localhost:8554/mystream_annotated
http://localhost:8888/mystream_annotated/index.m3u8
```

The dashboard plays only this annotated LL-HLS output. It does not compose a
second browser-side bounding-box layer, so video, geometry, and labels are
encoded on the same frame clock. It is normal for the annotated playlist to
return 404 briefly before the camera WebSocket has started its compositor.

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
- Per-stream inference health: `http://localhost:8000/health/streams`

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

### Multi-stream behavior detection

Live network sources use one decoded capture per camera. Pose/Behavior consumes
ordered source frames at `FALL_LIVE_FRAME_STRIDE=1`; PPE and Sign use
independent latest-only cadences. Never set the Behavior stride to 2: a
60-sample window would change from 2.5 seconds to 5 seconds and would no longer
match the trained classifier.

The default two-stream profile uses YOLO26s-pose at 448 pixels, finite CUDA
micro-batches, one authoritative per-camera Pose/BoT-SORT/ReID tracker, batched
CUDA ReID, fixed-camera GMC disabled, and the production CPU ExtraTrees
classifier. Live PPE uses detection only and maps its semantic result onto the
Pose track by IoU; it does not create another ByteTrack instance. The ordered
queue is bounded at 180 frames; overflow is reported as a discontinuity and
invalidates the current 60-frame window instead of silently classifying a
non-consecutive sequence. At 1280x720, a completely full queue can retain
roughly 475 MiB of raw BGR frames per camera, so lower the queue only if memory
pressure is more important than absorbing short inference bursts.

Useful `.env` controls are documented in `backend/.env.example`, including
`BEHAVIOR_POSE_IMGSZ`, `BEHAVIOR_BATCH_MAX_SIZE`,
`BEHAVIOR_CAMERA_BURST_SIZE`, `BEHAVIOR_REID_INTERVAL_FRAMES`, and the CPU
thread budgets. Restart the FastAPI process after changing them. Use
`GET /health/streams` to compare queue depth and pose/ReID/XGBoost/JPEG/WebSocket
timings without exposing RTSP credentials.

The server-side annotated stream is controlled by the `ANNOTATED_*` settings.
Defaults are a three-second presentation delay, an eight-frame PPE label TTL, a
three-second Sign TTL, automatic NVENC selection with libx264 fallback, and the
`_annotated` MediaMTX path suffix. Its health fields include publication rate,
queue depth/drop count, deadline misses, encoder restarts, and compose/publish
latencies.

Run a repeatable annotated-stream benchmark after opening the dashboard:

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\benchmark_llhls.py `
  --sources rtsp://127.0.0.1:8554/stream1 rtsp://127.0.0.1:8554/stream2 `
  --hls-path-suffix _annotated `
  --warmup 10 --duration 30 `
  --output benchmarks/annotated-llhls.json
```

### Email Reports

The Incident Analytics dashboard can export a PDF report and email it via
SMTP (`backend/app/services/reporting/`). Email delivery is **disabled by
default** — `GET /reports/incidents.pdf` and `GET /reports/incidents/preview`
work with no configuration, but `POST /reports/incidents/email` returns `503`
until SMTP is set up.

To enable it, add to `backend/.env`:

```dotenv
REPORT_EMAIL_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-account@gmail.com
SMTP_PASSWORD=your-16-char-app-password
SMTP_USE_STARTTLS=true
SMTP_FROM_EMAIL=safety-reports@yourcompany.com
REPORT_RECIPIENT_ALLOWLIST=["@yourcompany.com"]
```

**Gmail app-password caveat:** Gmail rejects your normal account password over
SMTP. `SMTP_PASSWORD` must be a 16-character
[App Password](https://myaccount.google.com/apppasswords), which requires
2-Step Verification to be enabled on the account first.

`REPORT_RECIPIENT_ALLOWLIST` is empty (allow any recipient) by default — set
it before enabling email delivery, since an unauthenticated endpoint with an
open recipient field is a spam-relay risk. See
`backend/docs/internal_deployment.md`. Verify the setup with:

```text
GET /health/smtp
```

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
- `GET /reports/incidents/preview`
- `GET /reports/incidents.pdf`
- `POST /reports/incidents/email`
- `GET /health/smtp`

## Documentation

- Backend setup: `backend/docs/backend_setup.md`
- Internal deployment notes: `backend/docs/internal_deployment.md`
- API details: `backend/docs/api_routes.md`
- Storage architecture: `backend/docs/storage_architecture.md`
- Approved database schema: `backend/docs/database_schema.dbml`
