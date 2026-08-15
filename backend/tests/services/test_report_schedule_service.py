from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.core.config import settings
from app.models.report_schedule import ReportSchedule
from app.services import ServiceValidationError
from app.services.reporting import ReportEmailError
from app.services.reporting.schedule_service import ReportScheduleService, run_due_schedule


def _repository(schedule: ReportSchedule | None = None) -> Mock:
    repo = Mock()
    repo.get_or_create.return_value = schedule or ReportSchedule(
        frequency="off", hour=7, minute=0, recipients="", include_snapshots=True,
    )
    repo.update.side_effect = lambda s: s
    return repo


# ---- ReportScheduleService.update validation ----


def test_update_rejects_invalid_frequency():
    service = ReportScheduleService(_repository(), Mock())

    with pytest.raises(ServiceValidationError):
        service.update(
            frequency="daily", day_of_week=None, day_of_month=None, hour=7, minute=0,
            zone_id=None, recipients=["a@b.com"], include_snapshots=True, subject=None, message=None,
        )


def test_update_weekly_requires_day_of_week():
    service = ReportScheduleService(_repository(), Mock())

    with pytest.raises(ServiceValidationError):
        service.update(
            frequency="weekly", day_of_week=None, day_of_month=None, hour=7, minute=0,
            zone_id=None, recipients=["a@b.com"], include_snapshots=True, subject=None, message=None,
        )


def test_update_monthly_requires_day_of_month_in_range():
    service = ReportScheduleService(_repository(), Mock())

    with pytest.raises(ServiceValidationError):
        service.update(
            frequency="monthly", day_of_week=None, day_of_month=31, hour=7, minute=0,
            zone_id=None, recipients=["a@b.com"], include_snapshots=True, subject=None, message=None,
        )


def test_update_requires_recipients_to_enable():
    service = ReportScheduleService(_repository(), Mock())

    with pytest.raises(ServiceValidationError):
        service.update(
            frequency="weekly", day_of_week=2, day_of_month=None, hour=7, minute=0,
            zone_id=None, recipients=[], include_snapshots=True, subject=None, message=None,
        )


def test_update_off_does_not_require_recipients():
    service = ReportScheduleService(_repository(), Mock())

    view = service.update(
        frequency="off", day_of_week=None, day_of_month=None, hour=7, minute=0,
        zone_id=None, recipients=[], include_snapshots=True, subject=None, message=None,
    )

    assert view.schedule.frequency == "off"
    assert view.next_run_at is None


def test_update_persists_weekly_schedule_and_recipients():
    repository = _repository()
    service = ReportScheduleService(repository, Mock())

    view = service.update(
        frequency="weekly", day_of_week=3, day_of_month=None, hour=8, minute=30,
        zone_id=5, recipients=["a@b.com", "a@b.com"], include_snapshots=False,
        subject="Weekly report", message="hi",
    )

    assert view.schedule.frequency == "weekly"
    assert view.schedule.day_of_week == 3
    assert view.schedule.hour == 8
    assert view.schedule.minute == 30
    assert view.schedule.zone_id == 5
    assert view.schedule.recipients == "a@b.com"  # deduped
    assert view.schedule.include_snapshots is False
    assert view.next_run_at is not None


def test_update_persists_vietnamese_language():
    repository = _repository()
    service = ReportScheduleService(repository, Mock())

    view = service.update(
        frequency="off", day_of_week=None, day_of_month=None, hour=7, minute=0,
        zone_id=None, recipients=[], include_snapshots=True, subject=None, message=None,
        language="vi",
    )

    assert view.schedule.language == "vi"


def test_update_falls_back_to_english_for_invalid_language():
    repository = _repository()
    service = ReportScheduleService(repository, Mock())

    view = service.update(
        frequency="off", day_of_week=None, day_of_month=None, hour=7, minute=0,
        zone_id=None, recipients=[], include_snapshots=True, subject=None, message=None,
        language="fr",
    )

    assert view.schedule.language == "en"


def test_update_rejects_bad_recipient():
    service = ReportScheduleService(_repository(), Mock())

    with pytest.raises(ServiceValidationError):
        service.update(
            frequency="weekly", day_of_week=2, day_of_month=None, hour=7, minute=0,
            zone_id=None, recipients=["a@b.com\nBcc: evil@x.com"], include_snapshots=True,
            subject=None, message=None,
        )


# ---- run_due_schedule ----


def test_run_due_schedule_skips_when_email_disabled(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", False)
    repository = _repository()
    report_service = Mock()

    result = run_due_schedule(repository, report_service)

    assert result is False
    report_service.email_report.assert_not_called()


def test_run_due_schedule_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    now = datetime.now(timezone.utc)
    schedule = ReportSchedule(
        frequency="weekly", day_of_week=now.weekday(), hour=now.hour, minute=now.minute,
        recipients="a@b.com", include_snapshots=True,
        last_sent_at=now,  # already sent for the current slot
    )
    repository = _repository(schedule)
    report_service = Mock()

    result = run_due_schedule(repository, report_service)

    assert result is False
    report_service.email_report.assert_not_called()


def test_run_due_schedule_sends_and_stamps_last_sent_at(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    now = datetime.now(timezone.utc)
    schedule = ReportSchedule(
        frequency="weekly", day_of_week=now.weekday(), hour=now.hour, minute=now.minute,
        recipients="a@b.com, c@d.com", include_snapshots=True, zone_id=None, last_sent_at=None,
    )
    repository = _repository(schedule)
    report_service = Mock()

    result = run_due_schedule(repository, report_service)

    assert result is True
    report_service.email_report.assert_called_once()
    call_kwargs = report_service.email_report.call_args.kwargs
    assert call_kwargs["range_"] == "7D"
    assert call_kwargs["recipients"] == ["a@b.com", "c@d.com"]
    assert schedule.last_sent_at is not None
    repository.update.assert_called_with(schedule)


def test_run_due_schedule_monthly_uses_30d_range(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    # last_sent_at=None guarantees a past monthly slot exists and is due,
    # regardless of what today's date happens to be.
    schedule = ReportSchedule(
        frequency="monthly", day_of_month=1, hour=0, minute=0,
        recipients="a@b.com", include_snapshots=True, last_sent_at=None,
    )
    repository = _repository(schedule)
    report_service = Mock()

    run_due_schedule(repository, report_service)

    assert report_service.email_report.call_args.kwargs["range_"] == "30D"


def test_run_due_schedule_does_not_stamp_last_sent_at_on_failure(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    now = datetime.now(timezone.utc)
    schedule = ReportSchedule(
        frequency="weekly", day_of_week=now.weekday(), hour=now.hour, minute=now.minute,
        recipients="a@b.com", include_snapshots=True, last_sent_at=None,
    )
    repository = _repository(schedule)
    report_service = Mock()
    report_service.email_report.side_effect = ReportEmailError("SMTP is down")

    result = run_due_schedule(repository, report_service)

    assert result is False
    assert schedule.last_sent_at is None
    repository.update.assert_not_called()


def test_run_due_schedule_skips_when_no_recipients(monkeypatch):
    monkeypatch.setattr(settings, "REPORT_EMAIL_ENABLED", True)
    now = datetime.now(timezone.utc)
    schedule = ReportSchedule(
        frequency="weekly", day_of_week=now.weekday(), hour=now.hour, minute=now.minute,
        recipients="", include_snapshots=True,
    )
    repository = _repository(schedule)
    report_service = Mock()

    result = run_due_schedule(repository, report_service)

    assert result is False
    report_service.email_report.assert_not_called()
