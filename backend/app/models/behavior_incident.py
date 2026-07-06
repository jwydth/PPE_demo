from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Relationship, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BehaviorType(str, Enum):
    FALL_DETECTED = "FALL_DETECTED"
    RUNNING_DETECTED = "RUNNING_DETECTED"
    FAINT_DETECTED = "FAINT_DETECTED"
    COLLAPSE_DETECTED = "COLLAPSE_DETECTED"


class BehaviorIncidentStatus(str, Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class BehaviorIncidentSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class BehaviorEvidenceType(str, Enum):
    SNAPSHOT = "SNAPSHOT"


class BehaviorIncident(SQLModel, table=True):
    __tablename__ = "behavior_incidents"
    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_behavior_incidents_confidence_range",
        ),
        CheckConstraint(
            "frame_start IS NULL OR frame_start >= 0",
            name="ck_behavior_incidents_frame_start_nonnegative",
        ),
        CheckConstraint(
            "frame_end IS NULL OR frame_end >= 0",
            name="ck_behavior_incidents_frame_end_nonnegative",
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
    behavior_type: str = Field(
        sa_column=Column(String(64), nullable=False, index=True),
    )
    status: str = Field(
        default=BehaviorIncidentStatus.NEW.value,
        sa_column=Column(
            String(32),
            nullable=False,
            server_default=BehaviorIncidentStatus.NEW.value,
            index=True,
        ),
    )
    severity: str | None = Field(
        default=BehaviorIncidentSeverity.HIGH.value,
        sa_column=Column(String(32), nullable=True),
    )
    confidence: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
    )
    track_id: int | None = Field(default=None, sa_type=BigInteger)
    frame_start: int | None = Field(default=None)
    frame_end: int | None = Field(default=None)
    started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    details: str = Field(sa_column=Column(Text, nullable=False))
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONB, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )

    subjects: list["BehaviorIncidentSubject"] = Relationship(
        back_populates="incident",
        cascade_delete=True,
        passive_deletes=True,
    )
    evidence: list["BehaviorEvidence"] = Relationship(
        back_populates="incident",
        cascade_delete=True,
        passive_deletes=True,
    )


class BehaviorIncidentSubject(SQLModel, table=True):
    __tablename__ = "behavior_incident_subjects"
    __table_args__ = (
        CheckConstraint(
            "person_index IS NULL OR person_index >= 0",
            name="ck_behavior_incident_subjects_person_index_nonnegative",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_behavior_incident_subjects_confidence_range",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    behavior_incident_id: int = Field(
        foreign_key="behavior_incidents.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    tracker_id: int | None = Field(default=None, sa_type=BigInteger)
    person_index: int | None = Field(default=None)
    bounding_box: dict[str, float] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    confidence: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
    )
    keypoints: list[Any] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    features: dict[str, Any] | None = Field(
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

    incident: BehaviorIncident = Relationship(back_populates="subjects")


class BehaviorEvidence(SQLModel, table=True):
    __tablename__ = "behavior_evidence"
    __table_args__ = (
        CheckConstraint(
            "frame_index IS NULL OR frame_index >= 0",
            name="ck_behavior_evidence_frame_index_nonnegative",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    behavior_incident_id: int = Field(
        foreign_key="behavior_incidents.id",
        ondelete="CASCADE",
        nullable=False,
        index=True,
        sa_type=BigInteger,
    )
    evidence_type: str = Field(
        default=BehaviorEvidenceType.SNAPSHOT.value,
        sa_column=Column(
            String(32),
            nullable=False,
            server_default=BehaviorEvidenceType.SNAPSHOT.value,
        ),
    )
    object_key: str = Field(sa_column=Column(String(500), nullable=False))
    frame_index: int | None = Field(default=None)
    occurred_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    created_at: datetime = Field(
        default_factory=_utc_now,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )

    incident: BehaviorIncident = Relationship(back_populates="evidence")
