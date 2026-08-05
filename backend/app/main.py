import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s",
)

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.runtime import configure_inference_runtime

configure_inference_runtime()

from app.routers import (
    analytics,
    cameras,
    detection,
    fall_detection,
    features,
    reports,
    streaming,
    testing,
    zones,
)
from app.services.reporting.scheduler import check_and_send_scheduled_report
from app.storage.local_paths import SNAPSHOT_DIR, ensure_snapshot_dir, ensure_upload_dir

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
    # Content-Disposition isn't in the CORS safelisted-response-header set, so
    # without this, cross-origin fetch() (frontend on :3000, backend on :8000)
    # can't read the report filename and downloadIncidentReportPdf() silently
    # falls back to a generic name.
    expose_headers=["Content-Disposition"],
)

app.include_router(analytics.router)
app.include_router(cameras.router)
app.include_router(detection.router)
app.include_router(fall_detection.router)
app.include_router(features.router)
app.include_router(reports.router)
app.include_router(streaming.router)
app.include_router(zones.router)
app.include_router(testing.router)
ensure_snapshot_dir()
app.mount("/snapshots", StaticFiles(directory=SNAPSHOT_DIR), name="snapshots")

# Checks every 15 minutes whether a weekly/monthly report schedule is due —
# coarser than minute-level cron, but run_due_schedule()'s date-math finds
# the most recent scheduled slot regardless of exact tick alignment, so
# nothing is missed. In-process only; does not survive multi-worker uvicorn
# (each worker would run its own checks) — see docs/internal_deployment.md.
_report_scheduler = BackgroundScheduler()


@app.on_event("startup")
async def startup() -> None:
    ensure_snapshot_dir()
    _report_scheduler.add_job(
        check_and_send_scheduled_report,
        "interval",
        minutes=15,
        id="report_schedule_check",
        replace_existing=True,
        max_instances=1,
    )
    _report_scheduler.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    _report_scheduler.shutdown(wait=False)
    from app.services.behavior_inference import shutdown_behavior_scheduler
    from app.services.frame_hub import frame_hubs

    await frame_hubs.close_all()
    await shutdown_behavior_scheduler()


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
