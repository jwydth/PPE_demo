from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.behavior_incident import (
    BehaviorEvidenceType,
    BehaviorIncidentSeverity,
    BehaviorIncidentStatus,
    BehaviorType,
)
from app.schemas.detection import BoundingBox


class BehaviorIncidentSubjectRead(BaseModel):
    id: int
    track_id: int | None = None
    person_index: int | None = None
    bounding_box: dict[str, float] | None = None
    confidence: float | None = None
    keypoints: list[Any] | None = None
    features: dict[str, Any] | None = None


class BehaviorEvidenceRead(BaseModel):
    id: int
    evidence_type: BehaviorEvidenceType
    object_key: str
    file_url: str | None = None
    frame_index: int | None = None
    timestamp: str


class BehaviorIncidentRead(BaseModel):
    id: int
    camera_id: int | None = None
    video_name: str | None = None
    behavior_type: BehaviorType
    status: BehaviorIncidentStatus
    severity: BehaviorIncidentSeverity | None = None
    confidence: float | None = None
    track_id: int | None = None
    frame_start: int | None = None
    frame_end: int | None = None
    timestamp: str
    ended_at: str | None = None
    details: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    snapshot_url: str | None = None
    subjects: list[BehaviorIncidentSubjectRead] = Field(default_factory=list)
    evidence: list[BehaviorEvidenceRead] = Field(default_factory=list)


class FallPoseDetection(BaseModel):
    track_id: int
    # ``unknown`` is a live-stream-only placeholder while a track is still
    # collecting its initial behavior-classifier window.
    status: Literal["unknown", "others", "running", "falling"]
    score: float
    person_confidence: float
    bbox: BoundingBox
    features: dict[str, float]
    keypoints: list[list[float]] | None = None
    incident_id: int | None = None


class FallDetectionSummary(BaseModel):
    status: Literal["others", "running", "falling", "no_detection"]
    others_count: int
    running_count: int
    falling_count: int
    person_count: int
    top_label: str
    top_confidence: float
    persisted_incident_ids: list[int] = Field(default_factory=list)


class FallVideoMetadata(BaseModel):
    source_fps: float
    sample_stride: int
    total_frames: int
    processed_frames: int
    frame_width: int
    frame_height: int


class FallTimelineItem(BaseModel):
    frame_index: int
    time_sec: float
    status: Literal["others", "running", "falling", "no_detection"]
    top_label: str
    top_confidence: float
    detections: list[FallPoseDetection]


class FallImagePredictionResponse(BaseModel):
    media_type: Literal["image"]
    device: str
    model_name: str
    model_version: str
    summary: FallDetectionSummary
    detections: list[FallPoseDetection]
    annotated_image: str | None = None
    incidents: list[BehaviorIncidentRead] = Field(default_factory=list)


class FallVideoPredictionResponse(BaseModel):
    media_type: Literal["video"]
    device: str
    model_name: str
    model_version: str
    summary: FallDetectionSummary
    video: FallVideoMetadata
    timeline: list[FallTimelineItem]
    incidents: list[BehaviorIncidentRead] = Field(default_factory=list)
