from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from app.models.camera import Camera


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PPEViolation(SQLModel, table=True):
    __tablename__ = "ppe_violations"
    __table_args__ = (
        CheckConstraint(
            "frame_index IS NULL OR frame_index >= 0",
            name="ck_ppe_violations_frame_index_nonnegative",
        ),
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
    source_key: str | None = Field(
        default=None,
        sa_column=Column(String(500), nullable=True, index=True),
    )
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    violation_type: str = Field(sa_column=Column(String(64), nullable=False))
    details: str = Field(sa_column=Column(Text, nullable=False))
    snapshot_path: str | None = Field(default=None)
    frame_index: int | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )

    camera: Optional["Camera"] = Relationship(
        back_populates="ppe_violations",
        passive_deletes=True,
    )
    subjects: list["PPEViolationSubject"] = Relationship(
        back_populates="ppe_violation",
        cascade_delete=True,
        passive_deletes=True,
    )


class PPEViolationSubject(SQLModel, table=True):
    __tablename__ = "ppe_violation_subjects"
    __table_args__ = (
        CheckConstraint(
            "person_index IS NULL OR person_index >= 0",
            name="ck_ppe_violation_subjects_person_index_nonnegative",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_ppe_violation_subjects_confidence_range",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    ppe_violation_id: int = Field(
        foreign_key="ppe_violations.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    tracker_id: int | None = Field(default=None, sa_type=BigInteger)
    person_index: int | None = Field(default=None)
    missing_equipment: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False),
    )
    bounding_box: dict[str, float] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    confidence: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )

    ppe_violation: "PPEViolation" = Relationship(back_populates="subjects")
