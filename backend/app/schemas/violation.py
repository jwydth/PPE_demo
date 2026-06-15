from typing import Optional

from pydantic import BaseModel, Field


class ViolationReport(BaseModel):
    id: int
    timestamp: str
    violation_type: str
    details: str
    missing_equipment: list[str] = Field(default_factory=list)
    snapshot_url: Optional[str] = None
    video_name: Optional[str] = None
    frame_index: Optional[int] = None
    track_id: Optional[int] = None


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
