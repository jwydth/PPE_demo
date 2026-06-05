from typing import Literal, Optional

from pydantic import BaseModel


class Zone(BaseModel):
    id: Optional[int] = None
    video_name: str
    zone_name: str
    zone_type: Literal["RESTRICTED", "WALKWAY", "FORKLIFT_PATH"]
    dwell_threshold_seconds: int = 0
    is_active: bool = True
    ui_shape_data: str  # JSON string
    flattened_coordinates: str  # JSON string


class CameraCalibration(BaseModel):
    video_name: str
    source_points: str  # JSON string of 4 normalized [x, y] points
