from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReportDelivery(SQLModel, table=True):
    __tablename__ = "report_deliveries"

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    report_type: str = Field(sa_column=Column(String(64), nullable=False))
    range_param: str = Field(sa_column=Column(String(16), nullable=False))
    # No FK constraint — zone rows may be deleted after a delivery is recorded;
    # this is an audit trail, not a live reference.
    zone_id: int | None = Field(default=None, sa_type=BigInteger)
    recipients: str = Field(sa_column=Column(String(1000), nullable=False))
    subject: str = Field(sa_column=Column(String(500), nullable=False))
    filename: str = Field(sa_column=Column(String(255), nullable=False))
    object_key: str | None = Field(default=None, sa_column=Column(String(500), nullable=True))
    size_bytes: int = Field(sa_column=Column(Integer, nullable=False))
    status: str = Field(sa_column=Column(String(16), nullable=False))
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
