from typing import Literal, Optional

from pydantic import BaseModel


class ZoneSuggestion(BaseModel):
    suggestion_id: str
    zone_type: str
    source_class: str
    confidence: float
    normalized_coordinates: list[dict]
    frame_index: int


class PPESuggestion(BaseModel):
    suggestion_id: str
    source_class: str
    confidence: float
    frame_index: int


class Zone(BaseModel):
    id: Optional[int] = None
    video_name: str
    zone_name: str
    zone_type: Literal["RESTRICTED", "WALKWAY"]
    dwell_threshold_seconds: float = 0.0
    is_active: bool = True
    ui_shape_data: str  # JSON string
    flattened_coordinates: str  # JSON string
