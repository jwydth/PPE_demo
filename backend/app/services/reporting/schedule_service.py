"""Recurring report schedule: validation + the periodic "is anything due"
check the background scheduler (see app/main.py) calls. Pure date-math lives
in schedule.py; this module adds the DB/service plumbing around it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends
from sqlmodel import Session

from app.core.config import settings
from app.db.session import get_session
from app.models.report_schedule import ReportSchedule
from app.repositories.report_schedule_repository import ReportScheduleRepository
from app.services import ServiceValidationError
from app.services.reporting import ReportEmailError
from app.services.reporting.report_service import (
    ReportService,
    get_report_service,
    validate_recipients,
)
from app.services.reporting.i18n import normalize_language
from app.services.reporting.schedule import is_due, next_run_at, report_range_for, resolve_timezone
from app.storage.evidence_storage import EvidenceStorage, get_evidence_storage

logger = logging.getLogger(__name__)

_VALID_FREQUENCIES = {"off", "weekly", "monthly"}


@dataclass(frozen=True)
class ScheduleView:
    schedule: ReportSchedule
    next_run_at: datetime | None


class ReportScheduleService:
    def __init__(self, repository: ReportScheduleRepository, report_service: ReportService) -> None:
        self.repository = repository
        self.report_service = report_service

    def get(self) -> ScheduleView:
        return self._to_view(self.repository.get_or_create())

    def update(
        self,
        *,
        frequency: str,
        day_of_week: int | None,
        day_of_month: int | None,
        hour: int,
        minute: int,
        zone_id: int | None,
        recipients: list[str],
        include_snapshots: bool,
        subject: str | None,
        message: str | None,
        language: str = "en",
    ) -> ScheduleView:
        if frequency not in _VALID_FREQUENCIES:
            raise ServiceValidationError("frequency must be 'off', 'weekly', or 'monthly'.")
        if frequency == "weekly" and (day_of_week is None or not 0 <= day_of_week <= 6):
            raise ServiceValidationError("day_of_week must be between 0 (Mon) and 6 (Sun).")
        if frequency == "monthly" and (day_of_month is None or not 1 <= day_of_month <= 28):
            raise ServiceValidationError("day_of_month must be between 1 and 28.")

        # Recipients are required to turn a schedule on; validate whatever
        # was submitted either way so a later "turn it back on" doesn't
        # silently reuse unvalidated data.
        clean_recipients = validate_recipients(recipients) if recipients else []
        if frequency != "off" and not clean_recipients:
            raise ServiceValidationError("At least one recipient is required to enable a schedule.")

        schedule = self.repository.get_or_create()
        schedule.frequency = frequency
        if frequency == "weekly":
            schedule.day_of_week = day_of_week
        if frequency == "monthly":
            schedule.day_of_month = day_of_month
        schedule.hour = hour
        schedule.minute = minute
        schedule.zone_id = zone_id
        if clean_recipients:
            schedule.recipients = ", ".join(clean_recipients)
        schedule.include_snapshots = include_snapshots
        schedule.subject = subject
        schedule.message = message
        schedule.language = normalize_language(language)
        return self._to_view(self.repository.update(schedule))

    def _to_view(self, schedule: ReportSchedule) -> ScheduleView:
        tz = resolve_timezone()
        return ScheduleView(
            schedule=schedule,
            next_run_at=next_run_at(schedule, datetime.now(timezone.utc), tz),
        )


def run_due_schedule(
    repository: ReportScheduleRepository, report_service: ReportService
) -> bool:
    """Called by the periodic background job (app/main.py). Returns True iff
    a report was actually sent this tick."""
    if not settings.REPORT_EMAIL_ENABLED:
        return False

    schedule = repository.get_or_create()
    tz = resolve_timezone()
    now = datetime.now(timezone.utc)
    if not is_due(schedule, now, tz):
        return False

    recipients = [r.strip() for r in schedule.recipients.split(",") if r.strip()]
    if not recipients:
        logger.warning("Report schedule is due but has no recipients configured; skipping.")
        return False

    range_ = report_range_for(schedule.frequency)
    try:
        report_service.email_report(
            range_=range_,
            zone_id=schedule.zone_id,
            recipients=recipients,
            subject=schedule.subject,
            message=schedule.message,
            include_snapshots=schedule.include_snapshots,
            language=schedule.language,
        )
    except (ServiceValidationError, ReportEmailError):
        # Don't stamp last_sent_at — retry on the next tick (every 15 min)
        # rather than silently waiting a full week/month. A persistently
        # broken config (bad recipient, SMTP down) shows up as repeated
        # FAILED rows in report_deliveries, which ReportService.email_report
        # already writes.
        logger.exception("Scheduled report send failed; will retry on the next check.")
        return False

    schedule.last_sent_at = now
    repository.update(schedule)
    return True


def get_report_schedule_service(
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[EvidenceStorage, Depends(get_evidence_storage)],
) -> ReportScheduleService:
    repository = ReportScheduleRepository(session)
    report_service = get_report_service(session, storage)
    return ReportScheduleService(repository, report_service)
