from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReportSchedule(SQLModel, table=True):
    """Site-wide recurring report config — a singleton row (id=1). The app
    has no auth/multi-tenancy, so one global schedule matches every other
    REPORT_* setting's scope."""

    __tablename__ = "report_schedules"

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    # "off" | "weekly" | "monthly"
    frequency: str = Field(
        default="off", sa_column=Column(String(16), nullable=False, server_default="off")
    )
    # 0=Monday .. 6=Sunday (Python date.weekday() convention). Used when
    # frequency == "weekly".
    day_of_week: int | None = Field(default=None)
    # 1-28. Used when frequency == "monthly" — clamped to 28 at write time so
    # every month has that day, avoiding "day 31 skips February" surprises.
    day_of_month: int | None = Field(default=None)
    hour: int = Field(default=7, sa_column=Column(Integer, nullable=False, server_default="7"))
    minute: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    # No FK constraint — an audit-adjacent config value, not a live reference.
    zone_id: int | None = Field(default=None, sa_type=BigInteger)
    recipients: str = Field(
        default="", sa_column=Column(String(1000), nullable=False, server_default="")
    )
    include_snapshots: bool = Field(default=True, nullable=False)
    subject: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    last_sent_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
        ),
    )
