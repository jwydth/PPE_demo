"""In-process periodic check for due recurring report schedules — the actual
APScheduler instance is wired up in app/main.py's startup/shutdown events.

Runs outside any HTTP request, so it constructs its own DB session and
service instances directly rather than going through FastAPI's request-scoped
Depends() chain (calling the same get_*() factory functions works fine
either way — Depends() markers are just default parameter values).
"""

import logging

from sqlmodel import Session

from app.db.session import get_engine
from app.repositories.report_schedule_repository import ReportScheduleRepository
from app.services.reporting.report_service import get_report_service
from app.services.reporting.schedule_service import run_due_schedule
from app.storage import StorageError
from app.storage.evidence_storage import get_evidence_storage

logger = logging.getLogger(__name__)


def check_and_send_scheduled_report() -> None:
    try:
        engine = get_engine()
    except RuntimeError:
        logger.debug("Report schedule check skipped: DATABASE_URL not configured.")
        return

    try:
        storage = get_evidence_storage()
    except StorageError:
        logger.warning("Report schedule check skipped: MinIO is not configured.")
        return

    try:
        with Session(engine) as session:
            repository = ReportScheduleRepository(session)
            report_service = get_report_service(session, storage)
            run_due_schedule(repository, report_service)
    except Exception:
        logger.exception("Report schedule check failed.")
