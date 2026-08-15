from typing import Literal

from pydantic import BaseModel, EmailStr, Field

ScheduleFrequency = Literal["off", "weekly", "monthly"]
ScheduleLanguage = Literal["en", "vi"]


class ReportScheduleRequest(BaseModel):
    frequency: ScheduleFrequency
    # 0=Monday .. 6=Sunday. Required (only) when frequency == "weekly".
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    # 1-28. Required (only) when frequency == "monthly".
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    hour: int = Field(default=7, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    zone_id: int | None = None
    recipients: list[EmailStr] = Field(default_factory=list)
    include_snapshots: bool = True
    subject: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=2000)
    language: ScheduleLanguage = "en"


class ReportScheduleResponse(BaseModel):
    frequency: ScheduleFrequency
    day_of_week: int | None
    day_of_month: int | None
    hour: int
    minute: int
    zone_id: int | None
    recipients: list[str]
    include_snapshots: bool
    subject: str | None
    message: str | None
    language: ScheduleLanguage
    last_sent_at: str | None
    next_run_at: str | None
    timezone_label: str
