from typing import Literal, Optional, Any
from pydantic import BaseModel
from app.schemas.detection import TrackingOverlayFrame, VideoSummary
from app.schemas.violation import ViolationReport, ZoneViolation

class StreamEvent(BaseModel):
    event: Literal["frame", "violation", "zone_violation", "zone_suggestion", "ppe_suggestion", "summary", "error", "start", "end"]
    frame_index: Optional[int] = None
    data: Any
    image_base64: Optional[str] = None
