from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.camera_zone_view import CameraZoneView
    from app.models.factory import Factory


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PhysicalZone(SQLModel, table=True):
    __tablename__ = "physical_zones"
    __table_args__ = (
        CheckConstraint(
            "dwell_threshold_seconds >= 0",
            name="ck_physical_zones_dwell_threshold_nonnegative",
        ),
        Index("ix_physical_zones_factory_id_name", "factory_id", "name"),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    factory_id: int = Field(
        foreign_key="factories.id",
        ondelete="RESTRICT",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    name: str = Field(sa_column=Column(String(255), nullable=False))
    zone_type: str = Field(sa_column=Column(String(32), nullable=False))
    floor_plan_polygon: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    dwell_threshold_seconds: float = Field(default=0.0, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    updated_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )

    factory: "Factory" = Relationship(back_populates="physical_zones")
    camera_zone_views: list["CameraZoneView"] = Relationship(
        back_populates="physical_zone",
        cascade_delete=True,
        passive_deletes=True,
    )
    cameras: list["Camera"] = Relationship(back_populates="home_zone")
