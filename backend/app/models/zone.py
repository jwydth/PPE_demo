from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.zone_violation import ZoneViolation


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Zone(SQLModel, table=True):
    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint(
            "zone_type IN ('RESTRICTED', 'WALKWAY', 'FORKLIFT_PATH')",
            name="ck_zones_zone_type",
        ),
        CheckConstraint(
            "dwell_threshold_seconds >= 0",
            name="ck_zones_dwell_threshold_nonnegative",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    camera_id: int = Field(
        foreign_key="cameras.id",
        ondelete="RESTRICT",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    name: str = Field(sa_column=Column(String(255), nullable=False))
    zone_type: str = Field(sa_column=Column(String(32), nullable=False))
    dwell_threshold_seconds: int = Field(default=0, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    ui_shape_data: dict[str, Any] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
    normalized_coordinates: list[dict[str, float]] = Field(
        sa_column=Column(JSONB, nullable=False),
    )
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

    camera: "Camera" = Relationship(back_populates="zones")
    zone_violations: list["ZoneViolation"] = Relationship(
        back_populates="zone",
        passive_deletes=True,
    )
