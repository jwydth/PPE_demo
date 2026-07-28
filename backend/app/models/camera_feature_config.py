from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, Column, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.feature import Feature


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CameraFeatureConfig(SQLModel, table=True):
    __tablename__ = "camera_feature_configs"
    __table_args__ = (
        UniqueConstraint(
            "camera_id",
            "feature_id",
            name="uq_camera_feature_configs_camera_feature",
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
    feature_id: int = Field(
        foreign_key="features.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    is_enabled: bool = Field(default=False, nullable=False)
    config_params: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
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

    camera: "Camera" = Relationship(back_populates="camera_feature_configs")
    feature: "Feature" = Relationship(back_populates="camera_feature_configs")
