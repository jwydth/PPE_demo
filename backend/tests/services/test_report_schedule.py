from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models.report_schedule import ReportSchedule
from app.services.reporting.schedule import (
    is_due,
    most_recent_slot,
    next_run_at,
    report_range_for,
)

UTC = timezone.utc
TZ_UTC = ZoneInfo("UTC")
TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")  # UTC+7, no DST


def _schedule(**overrides) -> ReportSchedule:
    defaults = dict(
        frequency="off",
        day_of_week=None,
        day_of_month=None,
        hour=7,
        minute=0,
        zone_id=None,
        recipients="",
        include_snapshots=True,
        subject=None,
        message=None,
        last_sent_at=None,
    )
    defaults.update(overrides)
    return ReportSchedule(**defaults)


# ---- weekly: most_recent_slot ----


def test_weekly_most_recent_slot_same_day_after_time_passed():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)  # today's weekday, 10:00
    schedule = _schedule(frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0)

    slot = most_recent_slot(schedule, now, TZ_UTC)

    assert slot == datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def test_weekly_most_recent_slot_same_day_before_time_steps_back_a_week():
    now = datetime(2026, 7, 29, 5, 0, tzinfo=UTC)  # today's weekday, but before 07:00
    schedule = _schedule(frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0)

    slot = most_recent_slot(schedule, now, TZ_UTC)

    assert slot == datetime(2026, 7, 22, 7, 0, tzinfo=UTC)


def test_weekly_most_recent_slot_finds_most_recent_past_weekday():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    target_weekday = (now.weekday() - 3) % 7  # 3 days before today
    schedule = _schedule(frequency="weekly", day_of_week=target_weekday, hour=7, minute=0)

    slot = most_recent_slot(schedule, now, TZ_UTC)

    assert slot == datetime(2026, 7, 26, 7, 0, tzinfo=UTC)
    assert slot.astimezone(TZ_UTC).date().weekday() == target_weekday


def test_weekly_missing_day_of_week_returns_none():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    schedule = _schedule(frequency="weekly", day_of_week=None)

    assert most_recent_slot(schedule, now, TZ_UTC) is None


# ---- monthly: most_recent_slot ----


def test_monthly_most_recent_slot_this_month_after_day_passed():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    schedule = _schedule(frequency="monthly", day_of_month=15, hour=7, minute=0)

    slot = most_recent_slot(schedule, now, TZ_UTC)

    assert slot == datetime(2026, 7, 15, 7, 0, tzinfo=UTC)


def test_monthly_most_recent_slot_steps_back_a_month_when_day_not_yet_reached():
    now = datetime(2026, 7, 5, 10, 0, tzinfo=UTC)
    schedule = _schedule(frequency="monthly", day_of_month=15, hour=7, minute=0)

    slot = most_recent_slot(schedule, now, TZ_UTC)

    assert slot == datetime(2026, 6, 15, 7, 0, tzinfo=UTC)


def test_monthly_day_31_is_clamped_and_never_raises_in_february():
    # 2026 is not a leap year, so February has only 28 days — this is the
    # one month where date(year, 2, 31) (or even 29) would raise ValueError
    # without the MAX_DAY_OF_MONTH clamp. now=March 1 forces the lookback
    # into February so the clamped Feb-28 slot is actually exercised.
    now = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    # Stored value bypassing the API-layer clamp — the pure function must
    # still defend against date(2026, 2, 31) raising ValueError.
    schedule = _schedule(frequency="monthly", day_of_month=31, hour=7, minute=0)

    slot = most_recent_slot(schedule, now, TZ_UTC)

    assert slot == datetime(2026, 2, 28, 7, 0, tzinfo=UTC)


def test_monthly_most_recent_slot_crosses_year_boundary_backwards():
    now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    schedule = _schedule(frequency="monthly", day_of_month=15, hour=7, minute=0)

    slot = most_recent_slot(schedule, now, TZ_UTC)

    assert slot == datetime(2025, 12, 15, 7, 0, tzinfo=UTC)


# ---- off / disabled ----


def test_off_frequency_has_no_slot_and_is_never_due():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    schedule = _schedule(frequency="off", day_of_week=2, hour=7, minute=0)

    assert most_recent_slot(schedule, now, TZ_UTC) is None
    assert is_due(schedule, now, TZ_UTC) is False
    assert next_run_at(schedule, now, TZ_UTC) is None


# ---- is_due ----


def test_is_due_true_when_never_sent():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    schedule = _schedule(
        frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0, last_sent_at=None
    )

    assert is_due(schedule, now, TZ_UTC) is True


def test_is_due_false_when_already_sent_for_this_slot():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    schedule = _schedule(
        frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0,
        last_sent_at=datetime(2026, 7, 29, 7, 0, tzinfo=UTC),
    )

    assert is_due(schedule, now, TZ_UTC) is False


def test_is_due_true_when_last_sent_was_a_prior_period():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    schedule = _schedule(
        frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0,
        last_sent_at=datetime(2026, 7, 22, 7, 0, tzinfo=UTC),
    )

    assert is_due(schedule, now, TZ_UTC) is True


def test_is_due_handles_naive_last_sent_at_as_utc():
    now = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
    schedule = _schedule(
        frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0,
        last_sent_at=datetime(2026, 7, 29, 7, 0),  # naive, e.g. from SQLite in tests
    )

    assert is_due(schedule, now, TZ_UTC) is False


# ---- next_run_at ----


def test_next_run_at_weekly_is_always_strictly_after_now():
    now = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)  # exactly at today's slot
    schedule = _schedule(frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0)

    result = next_run_at(schedule, now, TZ_UTC)

    assert result == datetime(2026, 8, 5, 7, 0, tzinfo=UTC)
    assert result > now


def test_next_run_at_weekly_later_today_if_time_not_yet_passed():
    now = datetime(2026, 7, 29, 5, 0, tzinfo=UTC)
    schedule = _schedule(frequency="weekly", day_of_week=now.weekday(), hour=7, minute=0)

    result = next_run_at(schedule, now, TZ_UTC)

    assert result == datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def test_next_run_at_monthly_crosses_year_boundary_forward():
    now = datetime(2026, 12, 20, 10, 0, tzinfo=UTC)
    schedule = _schedule(frequency="monthly", day_of_month=15, hour=7, minute=0)

    result = next_run_at(schedule, now, TZ_UTC)

    assert result == datetime(2027, 1, 15, 7, 0, tzinfo=UTC)


# ---- timezone conversion ----


def test_slot_converts_local_wall_clock_to_correct_utc_instant():
    # 07:00 in Asia/Ho_Chi_Minh (UTC+7) is 00:00 UTC the same calendar day.
    now = datetime(2026, 7, 29, 1, 0, tzinfo=UTC)  # 08:00 local on Jul 29
    schedule = _schedule(frequency="weekly", day_of_week=2, hour=7, minute=0)
    # Jul 29 2026 local date must match day_of_week=2 for this to resolve to
    # "today" — derive it from the actual local weekday instead of assuming.
    local_weekday = now.astimezone(TZ_VN).date().weekday()
    schedule.day_of_week = local_weekday

    slot = most_recent_slot(schedule, now, TZ_VN)

    expected_local = datetime(2026, 7, 29, 7, 0, tzinfo=TZ_VN)
    assert slot == expected_local.astimezone(UTC)
    assert slot.hour == 0  # 07:00 +7 -> 00:00 UTC


def test_report_range_for_weekly_and_monthly():
    assert report_range_for("weekly") == "7D"
    assert report_range_for("monthly") == "30D"
