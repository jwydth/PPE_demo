from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, String, func
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.zone import Zone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ZoneViolation(SQLModel, table=True):
    __tablename__ = "zone_violations"
    __table_args__ = (
        CheckConstraint(
            "frame_index >= 0",
            name="ck_zone_violations_frame_index_nonnegative",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    zone_id: int | None = Field(
        default=None,
        foreign_key="zones.id",
        ondelete="SET NULL",
        nullable=True,
        index=True,
        sa_type=BigInteger,
    )
    zone_name: str = Field(sa_column=Column(String(255), nullable=False))
    source_key: str = Field(
        sa_column=Column(String(500), nullable=False, index=True),
    )
    tracker_id: int | None = Field(default=None, sa_type=BigInteger)
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    frame_index: int = Field(nullable=False)
    snapshot_path: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )

    zone: Optional["Zone"] = Relationship(
        back_populates="zone_violations",
        passive_deletes=True,
    )
