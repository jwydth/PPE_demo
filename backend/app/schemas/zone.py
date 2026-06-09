from typing import Literal, Optional

from pydantic import BaseModel


class Zone(BaseModel):
    id: Optional[int] = None
    video_name: str
    zone_name: str
    zone_type: Literal["RESTRICTED", "WALKWAY"]
    dwell_threshold_seconds: float = 0.0
    is_active: bool = True
    ui_shape_data: str  # JSON string
    flattened_coordinates: str  # JSON string
