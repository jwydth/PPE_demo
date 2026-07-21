from datetime import datetime

from pydantic import BaseModel


class CameraRead(BaseModel):
    id: int
    name: str
    source_key: str
    source_uri: str | None
    home_zone_id: int | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class HomeZoneUpdate(BaseModel):
    zone_id: int | None


class CameraEnsure(BaseModel):
    """Idempotent get-or-create request, keyed by source_key."""

    name: str
    source_key: str
