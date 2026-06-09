from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Column, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.physical_zone import PhysicalZone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CameraZoneView(SQLModel, table=True):
    __tablename__ = "camera_zone_views"
    __table_args__ = (
        UniqueConstraint(
            "camera_id",
            "physical_zone_id",
            name="uq_camera_zone_views_camera_physical_zone",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    camera_id: int = Field(
        foreign_key="cameras.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    physical_zone_id: int = Field(
        foreign_key="physical_zones.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    ui_shape_data: dict[str, Any] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
    normalized_coordinates: list[dict[str, float]] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
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

    camera: "Camera" = Relationship(back_populates="camera_zone_views")
    physical_zone: "PhysicalZone" = Relationship(back_populates="camera_zone_views")
