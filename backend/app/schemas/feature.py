from typing import Any, Optional
from pydantic import BaseModel


class FeatureRead(BaseModel):
    id: int
    key: str
    name: str
    description: Optional[str] = None
    is_active: bool


class CameraFeatureConfigRead(BaseModel):
    id: int
    camera_id: int
    feature_key: str
    feature_name: str
    is_enabled: bool
    config_params: Optional[dict[str, Any]] = None


class CameraFeatureConfigUpdate(BaseModel):
    feature_key: str
    is_enabled: bool
    config_params: Optional[dict[str, Any]] = None
