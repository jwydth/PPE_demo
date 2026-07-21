from typing import Literal

from pydantic import BaseModel

IncidentCategory = Literal["ppe", "zone", "behavior"]
IncidentSeverity = Literal["Critical", "High", "Medium", "Low"]


class UnifiedIncidentRead(BaseModel):
    id: int
    category: IncidentCategory
    type: str
    severity: IncidentSeverity
    timestamp: str
    camera_id: int | None
    zone_id: int | None
    zone_name: str
    camera_label: str
    snapshot_url: str | None
