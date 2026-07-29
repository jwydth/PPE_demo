from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from app.schemas.analytics import AnalyticsRange, SeverityCounts


class ReportEmailRequest(BaseModel):
    recipients: list[EmailStr]  # pydantic validates shape; the service validates policy
    range: AnalyticsRange = "7D"
    zone_id: int | None = None
    subject: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=2000)
    include_snapshots: bool = True


class ReportEmailResponse(BaseModel):
    status: Literal["sent"]
    recipients: list[str]
    filename: str
    size_bytes: int
    object_key: str | None
    sent_at: str


class ReportPreview(BaseModel):
    range: AnalyticsRange
    zone_scope_label: str
    generated_at_local: str
    grand_total: int
    severity_counts: SeverityCounts
    insights: list[str]
    data_caveats: list[str]
