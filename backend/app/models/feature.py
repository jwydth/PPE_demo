from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Column, DateTime, String, func
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera_feature_config import CameraFeatureConfig


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Feature(SQLModel, table=True):
    __tablename__ = "features"

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    key: str = Field(
        sa_column=Column(String(50), nullable=False, unique=True, index=True),
    )
    name: str = Field(
        sa_column=Column(String(255), nullable=False),
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(String(500), nullable=True),
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

    camera_feature_configs: list["CameraFeatureConfig"] = Relationship(
        back_populates="feature",
        cascade_delete=True,
        passive_deletes=True,
    )
