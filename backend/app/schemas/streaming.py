from typing import Literal, Optional, Any
from pydantic import BaseModel, Field
from app.schemas.detection import TrackingOverlayFrame, VideoSummary
from app.schemas.violation import ViolationReport, ZoneViolation

class StreamEvent(BaseModel):
    event: Literal[
        "frame",
        "violation",
        "zone_violation",
        "zone_suggestion",
        "ppe_suggestion",
        "sign_prediction",
        "behavior_incident",
        "summary",
        "error",
        "start",
        "end",
    ]
    frame_index: Optional[int] = None
    stream_epoch: Optional[str] = None
    media_pts_ms: Optional[float] = None
    source_time_ms: Optional[float] = None
    inference_completed_ms: Optional[float] = None
    discontinuity_sequence: Optional[int] = None
    data: Any
    # True when a raw-JPEG binary WS frame for this event was (or is about to
    # be) sent on the same connection — see `image_bytes` below and
    # routers/streaming.py. Lets the client pair the binary frame with this
    # JSON envelope without embedding the image inline.
    has_image: bool = False
    # Populated by the pipeline for "frame" events on live streams; excluded
    # from JSON serialization on purpose — routers/streaming.py pulls it out
    # and sends it via `websocket.send_bytes` instead (PERF_PLAN.md Tier 2.2:
    # avoids ~33% base64 inflation + encode/decode cost of embedding it here).
    image_bytes: Optional[bytes] = Field(default=None, exclude=True)
