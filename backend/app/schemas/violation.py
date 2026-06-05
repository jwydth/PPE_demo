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


class ZoneViolation(BaseModel):
    id: Optional[int] = None
    zone_id: Optional[int] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    track_id: int
    timestamp: str
    video_name: str
    frame_index: int
    snapshot_path: Optional[str] = None
