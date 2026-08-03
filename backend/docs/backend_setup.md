# Backend Setup

The main project README is the source of truth for first-time setup.

Use:

```text
../../README.md
```

It includes:

- Docker Compose infrastructure setup for PostgreSQL and MinIO
- Backend virtual environment setup
- Recommended CUDA 13.2 runtime setup
- Fallback CUDA 12.8 runtime setup
- Backend `.env` setup
- Database initialization
- GPU runtime verification
- Frontend startup
- Local reset commands

The backend-specific commands are still run from this directory:

```powershell
cd C:\path\to\PPE_demo\backend
.\.venv\Scripts\Activate.ps1
python -m app.db.init_db
python scripts/check_gpu_runtime.py
python -m uvicorn app.main:app --reload --reload-dir app --port 8000
```

For two-camera behavior testing, publish both RTSP sources at 24 FPS and keep
`FALL_LIVE_FRAME_STRIDE=1`. The optimized defaults preserve 60 consecutive
samples, batch pose/ReID on CUDA, and keep XGBoost on CPU. After changing any
behavior setting in `.env`, restart Uvicorn and inspect:

```powershell
Invoke-RestMethod http://localhost:8000/health/streams |
    ConvertTo-Json -Depth 5
```

Healthy steady state has no increasing `behavior_queue_depth`, no recurring
`behavior_gap_events`, and stage timings comfortably below the source-frame
budget after batching. A nonzero gap is safe—the temporal session resets—but
repeated gaps mean the machine still cannot sustain the configured sources.
