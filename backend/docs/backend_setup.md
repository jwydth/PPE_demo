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
python -m uvicorn app.main:app --reload --port 8000
```
