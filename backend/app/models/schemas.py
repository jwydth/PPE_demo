from typing import List, Literal, Optional

from pydantic import BaseModel


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Detection(BaseModel):
    id: int
    label: str
    category: Literal["compliant", "violation"]
    confidence: float
    bbox: BoundingBox
    color: str


class EquipmentStatus(BaseModel):
    label: str                          # e.g. "Helmet", "Vest"
    status: Literal["compliant", "violation"]
    confidence: Optional[float] = None  # None when inferred as missing
    bbox: Optional[BoundingBox] = None  # None when inferred as missing


class PersonResult(BaseModel):
    person_id: int                      # 1-based for display
    bbox: BoundingBox                   # the person bounding box
    confidence: float
    equipment: List[EquipmentStatus]
    compliant: bool                     # True only if ALL equipment is present


class Summary(BaseModel):
    total_persons: int
    compliant: int
    violations: int
    inference_ms: float


class DetectionResponse(BaseModel):
    detections: List[Detection]         # flat list — for canvas drawing
    persons: List[PersonResult]         # grouped — for the results panel
    summary: Summary
