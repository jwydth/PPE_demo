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


class PersonTrackFrame(BaseModel):
    frame_index: int
    track_id: int
    bbox: BoundingBox  # normalized 0-1 relative to frame dimensions
    zone_id: Optional[int] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None  # "RESTRICTED" or "WALKWAY" when in violation state


class ZoneViolation(BaseModel):
    id: Optional[int] = None
    zone_id: int
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    track_id: int
    timestamp: str
    video_name: str
    frame_index: int
    snapshot_path: Optional[str] = None
    bbox: Optional[BoundingBox] = None  # normalized 0-1 coordinates at violation frame


class VideoProcessingResponse(BaseModel):
    summary: VideoSummary
    reports: List[ViolationReport]
    zone_violations: List[ZoneViolation] = []
    tracking_frames: List[PersonTrackFrame] = []


class Zone(BaseModel):
    id: Optional[int] = None
    video_name: str
    zone_name: str
    zone_type: Literal["RESTRICTED", "WALKWAY"]
    dwell_threshold_seconds: int = 0
    is_active: bool = True
    ui_shape_data: str  # JSON string
    flattened_coordinates: str  # JSON string
