import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routers import (
    analytics,
    cameras,
    detection,
    fall_detection,
    streaming,
    testing,
    zones,
)
from app.storage.local_paths import SNAPSHOT_DIR, ensure_snapshot_dir, ensure_upload_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)

app = FastAPI(
    title="De Heus PPE Detection API",
    description="Smart factory PPE compliance monitoring powered by YOLOv8",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analytics.router)
app.include_router(cameras.router)
app.include_router(detection.router)
app.include_router(fall_detection.router)
app.include_router(streaming.router)
app.include_router(zones.router)
app.include_router(testing.router)
ensure_snapshot_dir()
app.mount("/snapshots", StaticFiles(directory=SNAPSHOT_DIR), name="snapshots")


@app.on_event("startup")
async def startup() -> None:
    ensure_snapshot_dir()


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
