from typing import Optional

from pydantic import BaseModel


class ViolationReport(BaseModel):
    id: int
    timestamp: str
    violation_type: str
    details: str
    snapshot_url: Optional[str] = None
    video_name: Optional[str] = None
    frame_index: Optional[int] = None
    track_id: Optional[int] = None


class PPEViolationSubjectRead(BaseModel):
    id: int
    track_id: Optional[int] = None
    person_index: Optional[int] = None
    missing_equipment: list[str]
    bounding_box: Optional[dict] = None
    confidence: Optional[float] = None


class ViolationDetail(ViolationReport):
    subjects: list[PPEViolationSubjectRead] = []


class ZoneViolation(BaseModel):
    id: Optional[int] = None
    # Backward-compatible API alias for camera_zone_view_id.
    zone_id: Optional[int] = None
    camera_id: Optional[int] = None
    physical_zone_id: Optional[int] = None
    camera_zone_view_id: Optional[int] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    track_id: Optional[int] = None
    timestamp: str
    video_name: str
    frame_index: int
    snapshot_path: Optional[str] = None
    status: str = "OPEN"
    severity: Optional[str] = None
