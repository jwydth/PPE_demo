from typing import List, Literal, Optional

from pydantic import BaseModel


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Detection(BaseModel):
    id: int
    label: str
    category: Literal["compliant", "violation"]
    confidence: float
    bbox: BoundingBox
    color: str


class EquipmentStatus(BaseModel):
    label: str
    status: Literal["compliant", "violation"]
    confidence: Optional[float] = None
    bbox: Optional[BoundingBox] = None


class PersonResult(BaseModel):
    person_id: int
    track_id: Optional[int] = None
    bbox: BoundingBox
    confidence: float
    equipment: List[EquipmentStatus]
    compliant: bool


class Summary(BaseModel):
    total_persons: int
    compliant: int
    violations: int
    inference_ms: float


class DetectionResponse(BaseModel):
    detections: List[Detection]
    persons: List[PersonResult]
    summary: Summary


class ViolationReport(BaseModel):
    id: int
    timestamp: str
    violation_type: str
    details: str
    snapshot_url: Optional[str] = None
    video_name: Optional[str] = None
    frame_index: Optional[int] = None
    track_id: Optional[int] = None


class VideoSummary(BaseModel):
    video_name: str
    total_frames: int
    processed_frames: int
    fps: float
    duration_seconds: float
    unique_violations: int
    candidate_violations: int = 0
    inference_ms: float


class VideoProcessingResponse(BaseModel):
    summary: VideoSummary
    reports: List[ViolationReport]
