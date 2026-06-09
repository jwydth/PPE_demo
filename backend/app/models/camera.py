from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera_zone_view import CameraZoneView
    from app.models.factory import Factory
    from app.models.ppe_violation import PPEViolation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Camera(SQLModel, table=True):
    __tablename__ = "cameras"

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    factory_id: int = Field(
        foreign_key="factories.id",
        ondelete="RESTRICT",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    name: str = Field(sa_column=Column(String(255), nullable=False))
    source_key: str = Field(
        sa_column=Column(String(500), nullable=False, unique=True, index=True)
    )
    source_uri: str | None = Field(default=None)
    calibration_source_points: list[Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
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

    factory: "Factory" = Relationship(back_populates="cameras")
    camera_zone_views: list["CameraZoneView"] = Relationship(
        back_populates="camera",
        cascade_delete=True,
        passive_deletes=True,
    )
    ppe_violations: list["PPEViolation"] = Relationship(
        back_populates="camera",
        passive_deletes=True,
    )
