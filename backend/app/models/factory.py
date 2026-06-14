from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Column, DateTime, String, func
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.physical_zone import PhysicalZone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Factory(SQLModel, table=True):
    __tablename__ = "factories"

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    name: str = Field(
        sa_column=Column(String(255), nullable=False, unique=True),
    )
    location: str | None = Field(
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

    cameras: list["Camera"] = Relationship(back_populates="factory")
    physical_zones: list["PhysicalZone"] = Relationship(back_populates="factory")
