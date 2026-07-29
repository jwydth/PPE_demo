"""Pure date-math for recurring report schedules — no I/O, fully
unit-testable. All schedule times are wall-clock in settings.REPORT_TIMEZONE
(falls back to UTC if that zone can't be resolved, matching report_data.py's
ZoneInfoNotFoundError fallback).

day_of_week uses Python's date.weekday() convention: 0=Monday .. 6=Sunday.
day_of_month is clamped to 1-28 at the API boundary so every month has that
day — no "day 31 skips February" edge case to handle here.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings
from app.models.report_schedule import ReportSchedule

WEEKLY_RANGE = "7D"
MONTHLY_RANGE = "30D"
MAX_DAY_OF_MONTH = 28


def resolve_timezone(tz_name: str | None = None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or settings.REPORT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def report_range_for(frequency: str) -> str:
    return WEEKLY_RANGE if frequency == "weekly" else MONTHLY_RANGE


def timezone_label(tz: ZoneInfo) -> str:
    offset = datetime.now(tz).utcoffset()
    total_minutes = int(offset.total_seconds() // 60) if offset else 0
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d} ({tz.key})"


def most_recent_slot(schedule: ReportSchedule, now_utc: datetime, tz: ZoneInfo) -> datetime | None:
    """Most recent scheduled UTC datetime at or before now_utc, or None if
    frequency is "off" or missing its day field."""
    if schedule.frequency == "weekly":
        if schedule.day_of_week is None:
            return None
        local_now = now_utc.astimezone(tz)
        days_since = (local_now.date().weekday() - schedule.day_of_week) % 7
        candidate_date = local_now.date() - timedelta(days=days_since)
        candidate = _combine(candidate_date, schedule, tz)
        if candidate > local_now:
            candidate = _combine(candidate_date - timedelta(days=7), schedule, tz)
        return candidate.astimezone(timezone.utc)

    if schedule.frequency == "monthly":
        if schedule.day_of_month is None:
            return None
        local_now = now_utc.astimezone(tz)
        day = min(schedule.day_of_month, MAX_DAY_OF_MONTH)
        candidate_date = local_now.date().replace(day=day)
        candidate = _combine(candidate_date, schedule, tz)
        if candidate > local_now:
            candidate_date = _shift_month(candidate_date, -1).replace(day=day)
            candidate = _combine(candidate_date, schedule, tz)
        return candidate.astimezone(timezone.utc)

    return None


def next_run_at(schedule: ReportSchedule, now_utc: datetime, tz: ZoneInfo | None = None) -> datetime | None:
    """Next scheduled UTC datetime strictly after now_utc — display only."""
    tz = tz or resolve_timezone()

    if schedule.frequency == "weekly":
        if schedule.day_of_week is None:
            return None
        local_now = now_utc.astimezone(tz)
        days_ahead = (schedule.day_of_week - local_now.date().weekday()) % 7
        candidate_date = local_now.date() + timedelta(days=days_ahead)
        candidate = _combine(candidate_date, schedule, tz)
        if candidate <= local_now:
            candidate = _combine(candidate_date + timedelta(days=7), schedule, tz)
        return candidate.astimezone(timezone.utc)

    if schedule.frequency == "monthly":
        if schedule.day_of_month is None:
            return None
        local_now = now_utc.astimezone(tz)
        day = min(schedule.day_of_month, MAX_DAY_OF_MONTH)
        candidate_date = local_now.date().replace(day=day)
        candidate = _combine(candidate_date, schedule, tz)
        if candidate <= local_now:
            candidate_date = _shift_month(candidate_date, 1).replace(day=day)
            candidate = _combine(candidate_date, schedule, tz)
        return candidate.astimezone(timezone.utc)

    return None


def is_due(schedule: ReportSchedule, now_utc: datetime, tz: ZoneInfo | None = None) -> bool:
    tz = tz or resolve_timezone()
    slot = most_recent_slot(schedule, now_utc, tz)
    if slot is None:
        return False
    last_sent = schedule.last_sent_at
    if last_sent is not None:
        if last_sent.tzinfo is None:
            last_sent = last_sent.replace(tzinfo=timezone.utc)
        if last_sent >= slot:
            return False
    return True


def _combine(d: date, schedule: ReportSchedule, tz: ZoneInfo) -> datetime:
    return datetime(d.year, d.month, d.day, schedule.hour, schedule.minute, tzinfo=tz)


def _shift_month(d: date, delta_months: int) -> date:
    month_index = d.month - 1 + delta_months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)
