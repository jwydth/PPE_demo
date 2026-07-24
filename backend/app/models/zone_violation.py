from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, String, func
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ZoneViolation(SQLModel, table=True):
    __tablename__ = "zone_violations"
    __table_args__ = (
        CheckConstraint(
            "frame_index >= 0",
            name="ck_zone_violations_frame_index_nonnegative",
        ),
        # Matches real query shapes: status-filtered incident lists ordered by
        # recency, and date-range reads joined by camera (see PERF_PLAN.md
        # Tier 3.3).
        Index("ix_zone_violations_status_occurred_at", "status", "occurred_at"),
        Index("ix_zone_violations_occurred_at_camera_id", "occurred_at", "camera_id"),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    camera_id: int | None = Field(
        default=None,
        foreign_key="cameras.id",
        ondelete="SET NULL",
        nullable=True,
        index=True,
        sa_type=BigInteger,
    )
    physical_zone_id: int | None = Field(
        default=None,
        foreign_key="physical_zones.id",
        ondelete="SET NULL",
        nullable=True,
        index=True,
        sa_type=BigInteger,
    )
    camera_zone_view_id: int | None = Field(
        default=None,
        foreign_key="camera_zone_views.id",
        ondelete="SET NULL",
        nullable=True,
        index=True,
        sa_type=BigInteger,
    )
    zone_name: str = Field(sa_column=Column(String(255), nullable=False))
    zone_type: str = Field(sa_column=Column(String(32), nullable=False))
    source_key: str = Field(
        sa_column=Column(String(500), nullable=False, index=True),
    )
    tracker_id: int | None = Field(default=None, sa_type=BigInteger)
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    frame_index: int = Field(nullable=False)
    snapshot_path: str | None = Field(default=None)
    status: str = Field(
        default="OPEN",
        sa_column=Column(String(32), nullable=False, server_default="OPEN"),
    )
    severity: str | None = Field(
        default=None,
        sa_column=Column(String(32), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
