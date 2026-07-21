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
    bbox: tuple[float, float, float, float]  # normalized x1, y1, x2, y2 in [0, 1]


class PhysicalZoneRead(BaseModel):
    id: int
    name: str
    zone_type: str
    is_active: bool


class PhysicalZoneCreate(BaseModel):
    name: str


class Zone(BaseModel):
    id: Optional[int] = None
    video_name: str
    zone_name: str
    zone_type: Literal["RESTRICTED", "WALKWAY", "SLIPPERY"]
    dwell_threshold_seconds: float = 0.0
    is_active: bool = True
    ui_shape_data: str  # JSON string
    flattened_coordinates: str  # JSON string
